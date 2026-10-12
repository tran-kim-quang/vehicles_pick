import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from order_vehicles.booking.dialogue import DialogueDecision, DialogueManager
from order_vehicles.booking.models import CallSession
from order_vehicles.booking.repository import SessionRepository
from order_vehicles.speech.domain.errors import SpeechError

from .protocol import (
    InputAudioEnd,
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

    async def json(self, value: dict) -> None:
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
    ) -> None:
        self.runtime_service = runtime_service
        self.repository = repository
        self.dialogue = dialogue
        self.extractor = extractor
        self.clock = clock
        self._connections: set[_VoiceConnection] = set()

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
        )
        self._connections.add(connection)
        try:
            await connection.run(socket)
        finally:
            self._connections.discard(connection)


class _VoiceConnection:
    def __init__(
        self,
        runtime_service,
        repository: SessionRepository,
        dialogue: DialogueManager,
        extractor,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self.runtime_service = runtime_service
        self.repository = repository
        self.dialogue = dialogue
        self.extractor = extractor
        self.clock = clock
        self._background: set[asyncio.Task] = set()
        self._responses = ResponseTasks()
        self._generation = 0

    @property
    def active_task_count(self) -> int:
        return sum(not task.done() for task in self._background) + self._responses.active_count

    async def run(self, socket: SessionSocket) -> None:
        writer = _SerializedWriter(socket)
        session: CallSession | None = None
        turn_id: str | None = None
        frames: asyncio.Queue[bytes | None] | None = None
        try:
            first = parse_client_event(await socket.receive())
            if isinstance(first, SessionStart):
                session = self.repository.create(first.profile_ref)
                session.resume_token = secrets.token_urlsafe(24)
                self.repository.save(session, expected_revision=session.revision)
            elif isinstance(first, SessionResume):
                session = self.repository.get(first.session_id)
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
                if not session.last_asked_field:
                    session.last_asked_field = "name"
                    self.repository.save(session, expected_revision=session.revision)
                    await self._start_response(
                        writer,
                        runtime,
                        session,
                        DialogueDecision(
                            kind="ask",
                            field="name",
                            text="Xin chào. Bạn tên là gì?",
                        ),
                        self._generation,
                    )

                while True:
                    raw = await socket.receive()
                    if isinstance(raw, bytes):
                        if frames is None:
                            await self._error(writer, "INPUT_TURN_NOT_OPEN", "Binary audio requires an open input turn")
                        else:
                            try:
                                frames.put_nowait(raw)
                            except asyncio.QueueFull:
                                self._generation += 1
                                await self._error(
                                    writer,
                                    "INPUT_OVERFLOW",
                                    "Input audio buffer overflowed; repeat the turn",
                                    retryable=True,
                                )
                                while not frames.empty():
                                    frames.get_nowait()
                                frames.put_nowait(None)
                                frames = None
                                turn_id = None
                        continue
                    try:
                        event = parse_client_event(raw)
                    except ValidationError as exc:
                        await self._error(writer, "INVALID_EVENT", str(exc))
                        continue

                    if isinstance(event, InputAudioStart):
                        if frames is not None:
                            await self._error(writer, "INPUT_TURN_ACTIVE", "Input turns must be serial")
                            continue
                        await self._interrupt(writer)
                        turn_id = event.turn_id
                        frames = asyncio.Queue(maxsize=100)
                        generation = self._generation
                        self._track(
                            self._transcribe_turn(
                                runtime, writer, session, frames, generation
                            )
                        )
                    elif isinstance(event, InputAudioEnd):
                        if frames is None or event.turn_id != turn_id:
                            await self._error(writer, "INPUT_TURN_MISMATCH", "Input turn is not active")
                            continue
                        await frames.put(None)
                        frames = None
                        turn_id = None
                    elif isinstance(event, ResponseInterrupt):
                        await self._interrupt(writer)
                    elif isinstance(event, PlaybackFinished):
                        self.dialogue.mark_playback_finished(session, event.response_id)
                        self.repository.save(session, expected_revision=session.revision)
                    else:
                        await self._error(writer, "SESSION_ALREADY_STARTED", "Session is already active")
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
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
            if session is not None:
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
            async for event in runtime.transcribe(source()):
                if generation != self._generation:
                    return
                await writer.json(
                    {
                        "type": "transcript",
                        "kind": event.kind,
                        "final_kind": event.final_kind,
                        "text": event.text,
                    }
                )
                if event.kind == "final" and event.final_kind == "summary":
                    await self._process_summary(
                        runtime, writer, session, event.text, generation
                    )
                    return
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
            decision = self.dialogue.handle(session, result, self.clock())
            if generation != self._generation:
                return
            self.repository.save(session, expected_revision=expected_revision)
            if decision.kind == "complete" and decision.payload is not None:
                await writer.json(
                    {"type": "booking.confirmed", "payload": decision.payload}
                )
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
            self._emit_response(
                writer,
                runtime,
                decision,
                response_id,
                generation,
            ),
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
                "text": decision.text,
                "field": decision.field,
                "confirmation": confirmation,
            }
        )
        started_audio = False
        async for packet in runtime.speak(decision.text):
            if generation != self._generation:
                return
            if not started_audio:
                await writer.json(
                    {
                        "type": "assistant.audio.start",
                        "response_id": response_id,
                        "format": packet.format.model_dump(),
                    }
                )
                started_audio = True
            await writer.bytes(packet.data)
        if generation == self._generation:
            await writer.json(
                {
                    "type": "assistant.audio.end",
                    "response_id": response_id,
                    "confirmation": confirmation,
                }
            )

    async def _interrupt(self, writer: _SerializedWriter) -> None:
        old_response = self._responses.response_id
        self._generation += 1
        await self._responses.interrupt()
        if old_response:
            await writer.json(
                {"type": "assistant.response.interrupted", "response_id": old_response}
            )

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
