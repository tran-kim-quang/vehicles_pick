import asyncio
from datetime import datetime

import pytest

from order_vehicles.booking.dialogue import DialogueManager
from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, ExtractionResult
from order_vehicles.booking.repository import SessionRepository
from order_vehicles.voice.session import VoiceSessionService
from order_vehicles.speech.domain.models import AudioFormat, AudioPacket, TranscriptionEvent

from .fakes import FakeExtractor, FakeRuntime, FakeRuntimeService, FakeSocket


NOW = datetime.fromisoformat("2026-10-12T10:00:00+07:00")
START = {
    "type": "session.start",
    "version": "voice.booking.v1",
    "audio_format": {
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
    },
}


def service(runtime, extractor, uuid_factory):
    repository = SessionRepository(["bike", "car"], uuid_factory=uuid_factory)
    return VoiceSessionService(
        FakeRuntimeService(runtime),
        repository,
        DialogueManager(FieldRegistry(uuid_factory=uuid_factory)),
        extractor,
        clock=lambda: NOW,
    ), repository


async def start_turn(socket: FakeSocket, turn_id: str = "turn-1") -> None:
    await socket.push({"type": "input_audio.start", "turn_id": turn_id})
    await socket.push(b"\x00\x00" * 320)
    await socket.push({"type": "input_audio.end", "turn_id": turn_id})


@pytest.mark.asyncio
async def test_summary_creates_one_domain_turn(uuid_factory) -> None:
    runtime = FakeRuntime(["Tôi là Nam"])
    extractor = FakeExtractor([ExtractionResult(patch=BookingPatch(name="Nam"))])
    svc, _ = service(runtime, extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket)
    await socket.wait_for(lambda e: e["type"] == "assistant.response.started" and e.get("field") == "phone")
    await socket.push(None)
    await task
    assert runtime.transcribe_calls == 1
    assert extractor.calls == 1


@pytest.mark.asyncio
async def test_binary_outside_open_input_turn_is_rejected(uuid_factory) -> None:
    svc, _ = service(FakeRuntime(), FakeExtractor([]), uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await socket.push(b"orphan")
    error = await socket.wait_for(lambda e: e["type"] == "error")
    await socket.push(None)
    await task
    assert error["code"] == "INPUT_TURN_NOT_OPEN"


@pytest.mark.asyncio
async def test_event_for_another_session_is_rejected(uuid_factory) -> None:
    svc, _ = service(FakeRuntime(), FakeExtractor([]), uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await socket.push(
        {
            "type": "response.interrupt",
            "session_id": "00000000-0000-0000-0000-000000000099",
        }
    )
    error = await socket.wait_for(lambda e: e["type"] == "error")
    await socket.push(None)
    await task
    assert error["code"] == "SESSION_ID_MISMATCH"


@pytest.mark.asyncio
async def test_old_llm_result_after_interrupt_is_ignored(uuid_factory) -> None:
    gate = asyncio.Event()
    runtime = FakeRuntime(["Tôi là Nam"])
    extractor = FakeExtractor([ExtractionResult(patch=BookingPatch(name="Nam"))])
    extractor.gates = [gate]
    svc, repository = service(runtime, extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    started = await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket)
    while extractor.calls == 0:
        await asyncio.sleep(0)
    await socket.push({"type": "response.interrupt"})
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    session = repository.get(started["session_id"])
    await socket.push(None)
    await task
    assert session.revision == 0


@pytest.mark.asyncio
async def test_input_turns_are_serial(uuid_factory) -> None:
    svc, _ = service(FakeRuntime(["Nam"]), FakeExtractor([ExtractionResult()]), uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await socket.push({"type": "input_audio.start", "turn_id": "one"})
    await socket.push({"type": "input_audio.start", "turn_id": "two"})
    error = await socket.wait_for(lambda e: e["type"] == "error")
    await socket.push({"type": "input_audio.end", "turn_id": "one"})
    await socket.push(None)
    await task
    assert error["code"] == "INPUT_TURN_ACTIVE"


@pytest.mark.asyncio
async def test_playback_ack_unlocks_confirmation(uuid_factory) -> None:
    full = BookingPatch(
        name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"
    )
    runtime = FakeRuntime(["Tôi là Nam", "đúng"])
    extractor = FakeExtractor(
        [ExtractionResult(patch=full), ExtractionResult(intent="affirm")]
    )
    svc, _ = service(runtime, extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket, "details")
    audio_end = await socket.wait_for(lambda e: e["type"] == "assistant.audio.end" and e.get("confirmation"))
    await socket.push({"type": "assistant.playback.finished", "response_id": audio_end["response_id"]})
    await start_turn(socket, "confirm")
    confirmed = await socket.wait_for(lambda e: e["type"] == "booking.confirmed")
    await socket.push(None)
    await task
    assert confirmed["payload"]["fields"]["vehicle_type"]["value"] == "car"


@pytest.mark.asyncio
async def test_disconnect_stops_tasks_and_keeps_draft(uuid_factory) -> None:
    runtime = FakeRuntime()
    runtime.speak_gate = asyncio.Event()
    svc, repository = service(runtime, FakeExtractor([]), uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    started = await socket.wait_for(lambda e: e["type"] == "session.started")
    await socket.push(None)
    await task
    assert svc.active_task_count == 0
    assert svc.runtime_service.active == 0
    assert repository.get(started["session_id"]).detached_at == NOW


@pytest.mark.asyncio
async def test_invalid_resume_token_is_rejected(uuid_factory) -> None:
    svc, _ = service(FakeRuntime(), FakeExtractor([]), uuid_factory)
    first = FakeSocket()
    task = asyncio.create_task(svc.run(first))
    await first.push(START)
    started = await first.wait_for(lambda e: e["type"] == "session.started")
    await first.push(None)
    await task

    resumed = FakeSocket()
    task = asyncio.create_task(svc.run(resumed))
    await resumed.push(
        {
            "type": "session.resume",
            "version": "voice.booking.v1",
            "session_id": started["session_id"],
            "resume_token": "wrong",
            "audio_format": START["audio_format"],
        }
    )
    error = await resumed.wait_for(lambda e: e["type"] == "error")
    await task
    assert error["code"] == "INVALID_RESUME_TOKEN"


@pytest.mark.asyncio
async def test_old_audio_cannot_cross_new_response_boundary(uuid_factory) -> None:
    class ControlledRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(["Nam", "0901234567"])
            self.old_tail_gate = asyncio.Event()
            self.speak_count = 0

        async def speak(self, text):
            self.speak_count += 1
            yield AudioPacket(format=AudioFormat(sample_rate=24000), data=f"head-{self.speak_count}".encode())
            if self.speak_count == 2:
                await self.old_tail_gate.wait()
                yield AudioPacket(format=AudioFormat(sample_rate=24000), data=b"old-tail")

    runtime = ControlledRuntime()
    extractor = FakeExtractor(
        [
            ExtractionResult(patch=BookingPatch(name="Nam")),
            ExtractionResult(patch=BookingPatch(phone="0901234567")),
        ]
    )
    svc, _ = service(runtime, extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket, "name")
    await socket.wait_for(lambda e: e["type"] == "assistant.audio.start" and runtime.speak_count == 2)
    await socket.push({"type": "response.interrupt"})
    await start_turn(socket, "phone")
    await socket.wait_for(lambda e: e["type"] == "assistant.audio.end" and runtime.speak_count >= 3)
    runtime.old_tail_gate.set()
    await socket.push(None)
    await task
    assert b"old-tail" not in socket.sent


@pytest.mark.asyncio
async def test_provider_timeout_does_not_commit_partial(uuid_factory) -> None:
    class TimeoutRuntime(FakeRuntime):
        async def transcribe(self, frames):
            async for _ in frames:
                pass
            yield TranscriptionEvent(kind="partial", text="Tên là Nam")
            raise TimeoutError("upstream timed out")

    extractor = FakeExtractor([])
    svc, repository = service(TimeoutRuntime(), extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    started = await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket)
    await socket.wait_for(lambda e: e["type"] == "error" and e["code"] == "STT_FAILED")
    await socket.push(None)
    await task
    assert extractor.calls == 0
    assert repository.get(started["session_id"]).revision == 0


@pytest.mark.asyncio
async def test_empty_summary_is_reprompted_without_llm(uuid_factory) -> None:
    runtime = FakeRuntime([""])
    extractor = FakeExtractor([])
    svc, repository = service(runtime, extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    started = await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket)
    response = await socket.wait_for(
        lambda e: e["type"] == "assistant.response.started"
        and "chưa nghe rõ" in e["text"]
    )
    await socket.push(None)
    await task
    assert response["field"] == "name"
    assert extractor.calls == 0
    assert repository.get(started["session_id"]).revision == 0


@pytest.mark.asyncio
async def test_tts_failure_is_reported(uuid_factory) -> None:
    class FailingTTS(FakeRuntime):
        async def speak(self, text):
            if False:
                yield None
            raise TimeoutError("tts timeout")

    svc, _ = service(FailingTTS(), FakeExtractor([]), uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    error = await socket.wait_for(lambda e: e["type"] == "error" and e["code"] == "TTS_FAILED")
    await socket.push(None)
    await task
    assert error["retryable"] is True


@pytest.mark.asyncio
async def test_second_connection_cannot_resume_active_session(uuid_factory) -> None:
    svc, _ = service(FakeRuntime(), FakeExtractor([]), uuid_factory)
    first = FakeSocket()
    first_task = asyncio.create_task(svc.run(first))
    await first.push(START)
    started = await first.wait_for(lambda e: e["type"] == "session.started")
    second = FakeSocket()
    second_task = asyncio.create_task(svc.run(second))
    await second.push(
        {
            "type": "session.resume",
            "version": "voice.booking.v1",
            "session_id": started["session_id"],
            "resume_token": started["resume_token"],
            "audio_format": START["audio_format"],
        }
    )
    error = await second.wait_for(lambda e: e["type"] == "error")
    await second_task
    await first.push(None)
    await first_task
    assert error["code"] == "SESSION_ACTIVE"


@pytest.mark.asyncio
async def test_oversized_pcm_frame_aborts_turn(uuid_factory) -> None:
    runtime = FakeRuntime(["must-not-be-used"])
    extractor = FakeExtractor([])
    svc, _ = service(runtime, extractor, uuid_factory)
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    await socket.wait_for(lambda e: e["type"] == "session.started")
    await socket.push({"type": "input_audio.start", "turn_id": "large"})
    await socket.push(b"\x00\x00" * 3000)
    error = await socket.wait_for(lambda e: e["type"] == "error")
    await socket.push(None)
    await task
    assert error["code"] == "INPUT_AUDIO_INVALID"
    assert extractor.calls == 0


@pytest.mark.asyncio
async def test_interrupt_invalidates_unheard_confirmation(uuid_factory) -> None:
    full = BookingPatch(
        name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"
    )
    svc, repository = service(
        FakeRuntime(["details"]),
        FakeExtractor([ExtractionResult(patch=full)]),
        uuid_factory,
    )
    socket = FakeSocket()
    task = asyncio.create_task(svc.run(socket))
    await socket.push(START)
    started = await socket.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(socket)
    await socket.wait_for(
        lambda e: e["type"] == "assistant.audio.end" and e["confirmation"]
    )
    await socket.push({"type": "response.interrupt"})
    await socket.wait_for(lambda e: e["type"] == "assistant.response.interrupted")
    await socket.push(None)
    await task
    saved = repository.get(started["session_id"])
    assert saved.pending_confirmation_revision is None
    assert saved.playback_finished is False


@pytest.mark.asyncio
async def test_completed_payload_is_replayed_after_disconnect(uuid_factory) -> None:
    full = BookingPatch(
        name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"
    )
    runtime = FakeRuntime(["details", "yes"])
    svc, _ = service(
        runtime,
        FakeExtractor(
            [ExtractionResult(patch=full), ExtractionResult(intent="affirm")]
        ),
        uuid_factory,
    )
    first = FakeSocket()
    first_task = asyncio.create_task(svc.run(first))
    await first.push(START)
    started = await first.wait_for(lambda e: e["type"] == "session.started")
    await start_turn(first, "details")
    confirmation = await first.wait_for(
        lambda e: e["type"] == "assistant.audio.end" and e["confirmation"]
    )
    await first.push(
        {
            "type": "assistant.playback.finished",
            "response_id": confirmation["response_id"],
        }
    )
    await start_turn(first, "affirm")
    original = await first.wait_for(lambda e: e["type"] == "booking.confirmed")
    await first.push(None)
    await first_task

    resumed = FakeSocket()
    resumed_task = asyncio.create_task(svc.run(resumed))
    await resumed.push(
        {
            "type": "session.resume",
            "version": "voice.booking.v1",
            "session_id": started["session_id"],
            "resume_token": started["resume_token"],
            "audio_format": START["audio_format"],
        }
    )
    replayed = await resumed.wait_for(lambda e: e["type"] == "booking.confirmed")
    await resumed.push(None)
    await resumed_task
    assert replayed["payload"] == original["payload"]
