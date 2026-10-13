import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from order_vehicles.booking.dialogue import DialogueDecision, DialogueManager
from order_vehicles.booking.models import CallSession
from order_vehicles.booking.repository import SessionRepository
from order_vehicles.observability.timing import TurnTiming
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.greeting import GREETING_TEXT

from .protocol import (
    InputAudioEnd,
    InputAudioAbort,
    InputAudioStart,
    PlaybackFinished,
    ResponseInterrupt,
    SessionResume,
    SessionStart,
    parse_client_event,
)
from .tasks import ResponseTasks


class SessionSocket(Protocol):
    async def receive(self) -> dict | bytes: ...

    async def send_json(self, value: dict) -> None: ...

    async def send_bytes(self, value: bytes) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class _SerializedWriter:
    def __init__(self, socket: SessionSocket) -> None:
        self.socket = socket
        self.lock = asyncio.Lock()
        self.session_id: str | None = None

    def bind_session(self, session_id) -> None:
        self.session_id = str(session_id)

    async def json(self, value: dict) -> None:
        if self.session_id and "session_id" not in value:
            value = {**value, "session_id": self.session_id}
        async with self.lock:
            await self.socket.send_json(value)

    async def bytes(self, value: bytes) -> None:
        async with self.lock:
            await self.socket.send_bytes(value)


class VoiceSessionService:
    def __init__(
        self,
        runtime_service,
        repository: SessionRepository,
        dialogue: DialogueManager,
        extractor,
        *,
        clock: Callable[[], datetime],
        responder=None,
        timing_recorder=None,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.runtime_service = runtime_service
        self.repository = repository
        self.dialogue = dialogue
        self.extractor = extractor
        self.responder = responder
        self.clock = clock
        self.timing_recorder = timing_recorder
        self.monotonic = monotonic
        self._connections: set[_VoiceConnection] = set()
        self._active_sessions: dict[UUID, str] = {}
        self._session_lock = asyncio.Lock()

    @property
    def active_task_count(self) -> int:
        return sum(connection.active_task_count for connection in self._connections)

    async def run(self, socket: SessionSocket) -> None:
        connection = _VoiceConnection(
            self.runtime_service,
            self.repository,
            self.dialogue,
            self.extractor,
            clock=self.clock,
            responder=self.responder,
            timing_recorder=self.timing_recorder,
            monotonic=self.monotonic,
            claim_session=self._claim_session,
            release_session=self._release_session,
        )
        self._connections.add(connection)
        try:
            await connection.run(socket)
        finally:
            self._connections.discard(connection)

    async def _claim_session(self, session_id: UUID, owner: str) -> bool:
        async with self._session_lock:
            if session_id in self._active_sessions:
                return False
            self._active_sessions[session_id] = owner
            return True

    async def _release_session(self, session_id: UUID, owner: str) -> bool:
        async with self._session_lock:
            if self._active_sessions.get(session_id) != owner:
                return False
            del self._active_sessions[session_id]
            return True


class _VoiceConnection:
    def __init__(
        self,
        runtime_service,
        repository: SessionRepository,
        dialogue: DialogueManager,
        extractor,
        *,
        clock: Callable[[], datetime],
        responder=None,
        timing_recorder=None,
        monotonic: Callable[[], float] = time.perf_counter,
        claim_session=None,
        release_session=None,
    ) -> None:
        self.runtime_service = runtime_service
        self.repository = repository
        self.dialogue = dialogue
        self.extractor = extractor
        self.responder = responder
        self.clock = clock
        self.timing_recorder = timing_recorder
        self.monotonic = monotonic
        self.claim_session = claim_session
        self.release_session = release_session
        self.owner = secrets.token_urlsafe(16)
        self._background: set[asyncio.Task] = set()
        self._responses = ResponseTasks()
        self._generation = 0
        self._timings: dict[int, TurnTiming] = {}
        self._turn_ids: dict[int, str] = {}

    @property
    def active_task_count(self) -> int:
        return sum(not task.done() for task in self._background) + self._responses.active_count

    async def run(self, socket: SessionSocket) -> None:
        writer = _SerializedWriter(socket)
        session: CallSession | None = None
        turn_id: str | None = None
        frames: asyncio.Queue[bytes | None] | None = None
        turn_bytes = 0
        claimed = False
        try:
            first = parse_client_event(await socket.receive())
            if isinstance(first, SessionStart):
                session = self.repository.create(first.profile_ref)
                session.resume_token = secrets.token_urlsafe(24)
                self.repository.save(session, expected_revision=session.revision)
            elif isinstance(first, SessionResume):
                session = self.repository.get(first.session_id, self.clock())
                if not secrets.compare_digest(session.resume_token or "", first.resume_token):
                    await self._error(writer, "INVALID_RESUME_TOKEN", "Resume token is invalid")
                    await socket.close(code=1008)
                    return
                session.detached_at = None
                self.repository.save(session, expected_revision=session.revision)
            else:
                await self._error(writer, "SESSION_START_REQUIRED", "First event must start or resume a session")
                await socket.close(code=1008)
                return

            if self.claim_session and not await self.claim_session(session.session_id, self.owner):
                await self._error(writer, "SESSION_ACTIVE", "Session already has an active connection")
                await socket.close(code=1008)
                return
            claimed = True
            writer.bind_session(session.session_id)
            await writer.json(
                {
                    "type": "session.started",
                    "version": "voice.booking.v1",
                    "session_id": str(session.session_id),
                    "request_id": str(session.request_id),
                    "resume_token": session.resume_token,
                }
            )

            async with self.runtime_service.open() as runtime:
                if session.status.value == "COMPLETED" and session.final_payload:
                    await writer.json(
                        {"type": "booking.confirmed", "payload": session.final_payload}
                    )
                    await self._start_response(
                        writer,
                        runtime,
                        session,
                        DialogueDecision(
                            kind="complete", text="Thông tin đã được xác nhận."
                        ),
                        self._generation,
                    )
                elif not session.last_asked_field:
                    session.last_asked_field = "name"
                    self.repository.save(session, expected_revision=session.revision)
                    await self._start_response(
                        writer,
                        runtime,
                        session,
                        DialogueDecision(
                            kind="ask",
                            text=GREETING_TEXT,
                        ),
                        self._generation,
                    )

                while True:
                    raw = await socket.receive()
                    if isinstance(raw, bytes):
                        if frames is None:
                            await self._error(writer, "INPUT_TURN_NOT_OPEN", "Binary audio requires an open input turn")
                        else:
                            if len(raw) % 2 or len(raw) > 4096 or turn_bytes + len(raw) > 960_000:
                                await self._abort_turn(
                                    writer, frames, "INPUT_AUDIO_INVALID", "PCM frame or turn exceeds its byte budget"
                                )
                                frames = None
                                turn_id = None
                                turn_bytes = 0
                                continue
                            try:
                                frames.put_nowait(raw)
                                turn_bytes += len(raw)
                            except asyncio.QueueFull:
                                await self._abort_turn(
                                    writer,
                                    frames,
                                    "INPUT_OVERFLOW",
                                    "Input audio buffer overflowed; repeat the turn",
                                )
                                frames = None
                                turn_id = None
                                turn_bytes = 0
                        continue
                    try:
                        event = parse_client_event(raw)
                    except ValidationError as exc:
                        await self._error(writer, "INVALID_EVENT", str(exc))
                        continue
                    event_session_id = getattr(event, "session_id", None)
                    if event_session_id and event_session_id != session.session_id:
                        await self._error(
                            writer,
                            "SESSION_ID_MISMATCH",
                            "Event session_id does not match this socket",
                        )
                        continue

                    if isinstance(event, InputAudioStart):
                        if session.status.value in {"COMPLETED", "CANCELLED", "NEEDS_ASSISTANCE"}:
                            await self._error(writer, "SESSION_TERMINAL", "Terminal sessions do not accept new turns")
                            continue
                        if frames is not None:
                            await self._error(writer, "INPUT_TURN_ACTIVE", "Input turns must be serial")
                            continue
                        await self._interrupt(writer, session)
                        turn_id = event.turn_id
                        turn_bytes = 0
                        frames = asyncio.Queue(maxsize=100)
                        generation = self._generation
                        self._turn_ids[generation] = event.turn_id
                        self._timings[generation] = TurnTiming(clock_domain="server")
                        self._track(
                            self._transcribe_turn(
                                runtime, writer, session, frames, generation
                            )
                        )
                    elif isinstance(event, InputAudioEnd):
                        if frames is None or event.turn_id != turn_id:
                            await self._error(writer, "INPUT_TURN_MISMATCH", "Input turn is not active")
                            continue
                        try:
                            frames.put_nowait(None)
                        except asyncio.QueueFull:
                            await self._abort_turn(writer, frames, "INPUT_OVERFLOW", "Input queue is full")
                        timing = self._timings.get(self._generation)
                        if timing:
                            timing.mark("input.end", self.monotonic())
                        frames = None
                        turn_id = None
                        turn_bytes = 0
                    elif isinstance(event, InputAudioAbort):
                        if frames is None or event.turn_id != turn_id:
                            await self._error(writer, "INPUT_TURN_MISMATCH", "Input turn is not active")
                            continue
                        await self._abort_turn(writer, frames, "INPUT_ABORTED", event.reason)
                        frames = None
                        turn_id = None
                        turn_bytes = 0
                    elif isinstance(event, ResponseInterrupt):
                        await self._interrupt(writer, session)
                    elif isinstance(event, PlaybackFinished):
                        self.dialogue.mark_playback_finished(session, event.response_id)
                        self.repository.save(session, expected_revision=session.revision)
                    else:
                        await self._error(writer, "SESSION_ALREADY_STARTED", "Session is already active")
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except SpeechError as exc:
            with suppress(Exception):
                await self._error(writer, exc.code, exc.message, exc.retryable)
                if session is None:
                    await socket.close(code=1008)
        except Exception as exc:
            if session is None:
                with suppress(Exception):
                    await self._error(writer, "INVALID_SESSION_START", str(exc))
                    await socket.close(code=1008)
            # Socket disconnects and receive errors end the in-memory attachment.
        finally:
            if frames is not None:
                with suppress(asyncio.QueueFull):
                    frames.put_nowait(None)
            await self._close_tasks()
            released = bool(session is not None and claimed)
            if released and self.release_session:
                released = await self.release_session(session.session_id, self.owner)
            if session is not None and released:
                with suppress(Exception):
                    self.repository.detach(session.session_id, self.clock())

    async def _transcribe_turn(
        self,
        runtime,
        writer: _SerializedWriter,
        session: CallSession,
        frames: asyncio.Queue[bytes | None],
        generation: int,
    ) -> None:
        async def source() -> AsyncIterator[bytes]:
            while True:
                frame = await frames.get()
                if frame is None:
                    break
                yield frame

        try:
            saw_summary = False
            async for event in runtime.transcribe(source()):
                if generation != self._generation:
                    return
                await writer.json(
                    {
                        "type": f"transcript.{event.kind}",
                        "kind": event.kind,
                        "final_kind": event.final_kind,
                        "text": event.text,
                        "turn_id": self._turn_ids.get(generation),
                    }
                )
                if event.kind == "final" and event.final_kind == "summary":
                    saw_summary = True
                    if not event.text.strip():
                        await self._repeat_prompt(runtime, writer, session, generation)
                        return
                    timing = self._timings.get(generation)
                    if timing:
                        timing.mark("stt.final", self.monotonic())
                    await self._process_summary(
                        runtime, writer, session, event.text, generation
                    )
                    return
            if not saw_summary and generation == self._generation:
                await self._repeat_prompt(runtime, writer, session, generation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self._generation:
                await self._error(writer, "STT_FAILED", str(exc), retryable=True)

    async def _process_summary(
        self,
        runtime,
        writer: _SerializedWriter,
        session: CallSession,
        transcript: str,
        generation: int,
    ) -> None:
        try:
            result = await self.extractor.extract(transcript, session.model_copy(deep=True))
            if generation != self._generation:
                return
            expected_revision = session.revision
            previous_fields = {name: field.value for name, field in session.fields.items() if field.valid}
            decision = self.dialogue.handle(session, result, self.clock())
            timing = self._timings.get(generation)
            if timing:
                timing.mark("llm.first_text", self.monotonic())
            if generation != self._generation:
                return
            self.repository.save(session, expected_revision=expected_revision)
            if decision.kind == "complete" and decision.payload is not None:
                await writer.json(
                    {"type": "booking.confirmed", "payload": decision.payload}
                )
            if self.responder is not None:
                decision = await self.responder.respond(
                    transcript, session.model_copy(deep=True), decision, previous_fields
                )
                if generation != self._generation:
                    return
            await self._start_response(writer, runtime, session, decision, generation)
        except asyncio.CancelledError:
            raise
        except SpeechError as exc:
            if generation == self._generation:
                await self._error(writer, exc.code, exc.message, exc.retryable)
        except Exception as exc:
            if generation == self._generation:
                await self._error(writer, "LLM_FAILED", str(exc), retryable=True)

    async def _start_response(
        self,
        writer: _SerializedWriter,
        runtime,
        session: CallSession,
        decision: DialogueDecision,
        generation: int,
    ) -> None:
        response_id = (
            session.confirmation_response_id
            if decision.kind == "confirm" and session.confirmation_response_id
            else str(uuid4())
        )
        await self._responses.start(
            response_id,
            self._response_work(
                writer,
                runtime,
                decision,
                response_id,
                generation,
            ),
        )

    async def _response_work(
        self, writer, runtime, decision, response_id: str, generation: int
    ) -> None:
        try:
            await self._emit_response(
                writer, runtime, decision, response_id, generation
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self._generation:
                await self._error(writer, "TTS_FAILED", str(exc), retryable=True)
                await writer.json(
                    {
                        "type": "assistant.response.failed",
                        "response_id": response_id,
                    }
                )

    async def _repeat_prompt(self, runtime, writer, session, generation) -> None:
        await self._start_response(
            writer,
            runtime,
            session,
            DialogueDecision(
                kind="ask",
                field=session.last_asked_field,
                text="Tôi chưa nghe rõ. Bạn vui lòng nói lại.",
            ),
            generation,
        )

    async def _emit_response(
        self,
        writer: _SerializedWriter,
        runtime,
        decision: DialogueDecision,
        response_id: str,
        generation: int,
    ) -> None:
        confirmation = decision.kind == "confirm"
        await writer.json(
            {
                "type": "assistant.response.started",
                "response_id": response_id,
                "turn_id": self._turn_ids.get(generation),
                "text": decision.text,
                "field": decision.field,
                "confirmation": confirmation,
            }
        )
        await writer.json(
            {
                "type": "assistant.text",
                "response_id": response_id,
                "turn_id": self._turn_ids.get(generation),
                "text": decision.text,
            }
        )
        started_audio = False
        async for packet in runtime.speak(decision.text):
            if generation != self._generation:
                return
            if not started_audio:
                timing = self._timings.get(generation)
                if timing:
                    timing.mark("tts.first_audio", self.monotonic())
                await writer.json(
                    {
                        "type": "assistant.audio.start",
                        "response_id": response_id,
                        "turn_id": self._turn_ids.get(generation),
                        "format": packet.format.model_dump(),
                    }
                )
                started_audio = True
            # Upstreams may return a full WAV as one PCM packet. Keep wire packets
            # below the client's two-second queue, including cached greeting audio.
            chunk_bytes = max(2 * packet.format.channels, packet.format.sample_rate // 10 * 2 * packet.format.channels)
            for offset in range(0, len(packet.data), chunk_bytes):
                if generation != self._generation:
                    return
                await writer.bytes(packet.data[offset:offset + chunk_bytes])
        if generation == self._generation:
            await writer.json(
                {
                    "type": "assistant.audio.end",
                    "response_id": response_id,
                    "turn_id": self._turn_ids.get(generation),
                    "confirmation": confirmation,
                }
            )
            timing = self._timings.pop(generation, None)
            if timing and self.timing_recorder:
                profile = getattr(runtime, "profile", None)
                self.timing_recorder.record(
                    timing,
                    {
                        "profile_revision": getattr(profile, "revision", None),
                        "engine": getattr(
                            getattr(profile, "tts", None), "engine_id", None
                        ),
                        "model": getattr(
                            getattr(profile, "tts", None), "model_id", None
                        ),
                    },
                )

    async def _interrupt(self, writer: _SerializedWriter, session: CallSession) -> None:
        old_response = self._responses.response_id
        old_generation = self._generation
        self._generation += 1
        self._timings.pop(old_generation, None)
        self._turn_ids.pop(old_generation, None)
        await self._responses.interrupt()
        if (
            old_response
            and old_response == session.confirmation_response_id
            and not session.playback_finished
        ):
            self.dialogue.invalidate_confirmation(session)
            self.repository.save(session, expected_revision=session.revision)
        if old_response:
            await writer.json(
                {"type": "assistant.response.interrupted", "response_id": old_response}
            )

    async def _abort_turn(self, writer, frames, code: str, message: str) -> None:
        old_generation = self._generation
        self._generation += 1
        self._timings.pop(old_generation, None)
        self._turn_ids.pop(old_generation, None)
        while not frames.empty():
            frames.get_nowait()
        frames.put_nowait(None)
        await self._error(writer, code, message, retryable=True)

    def _track(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _close_tasks(self) -> None:
        await self._responses.close()
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background.clear()
        self._timings.clear()
        self._turn_ids.clear()

    @staticmethod
    async def _error(
        writer: _SerializedWriter,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        await writer.json(
            {
                "type": "error",
                "code": code,
                "message": message,
                "retryable": retryable,
            }
        )
