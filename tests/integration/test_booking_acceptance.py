from datetime import datetime
from uuid import UUID

import pytest

from order_vehicles.booking.dialogue import DialogueManager
from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, CallSession, ExtractionResult, ScheduleProposal
from order_vehicles.observability.timing import TimingRecorder, TurnTiming
from order_vehicles.speech.domain.models import RuntimeProfile, VoiceRef


NOW = datetime.fromisoformat("2026-10-12T10:00:00+07:00")


def dialogue_session(uuid_factory):
    session = CallSession.create(["bike", "car"], uuid_factory=uuid_factory)
    return DialogueManager(FieldRegistry(uuid_factory=uuid_factory)), session


def test_all_fields_one_turn_confirms_uuid_json(uuid_factory) -> None:
    dialogue, session = dialogue_session(uuid_factory)
    full = BookingPatch(
        name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="ô tô"
    )
    readback = dialogue.handle(session, ExtractionResult(patch=full), NOW)
    assert readback.kind == "confirm"
    dialogue.mark_playback_finished(session, session.confirmation_response_id)
    completed = dialogue.handle(session, ExtractionResult(intent="affirm"), NOW)
    assert completed.kind == "complete"
    assert completed.payload["fields"]["vehicle_type"]["value"] == "car"
    assert completed.payload["revision"] == completed.payload["confirmed_revision"]
    for field in completed.payload["fields"].values():
        UUID(field["id"])


def test_scheduled_missing_time_and_unavailable_vehicle_are_asked(uuid_factory) -> None:
    dialogue, session = dialogue_session(uuid_factory)
    missing_time = BookingPatch(
        name="Nam",
        phone="0901234567",
        booking_mode="scheduled",
        schedule=ScheduleProposal(relative_days=1),
        vehicle_choice="helicopter",
    )
    decision = dialogue.handle(session, ExtractionResult(patch=missing_time), NOW)
    assert decision.kind == "ask" and decision.field == "scheduled_at"
    decision = dialogue.handle(
        session,
        ExtractionResult(
            patch=BookingPatch(
                schedule=ScheduleProposal(relative_days=1, hour=14, period="24h")
            )
        ),
        NOW,
    )
    assert decision.kind == "ask" and decision.field == "vehicle_type"


def test_correction_during_confirmation_requires_new_readback(uuid_factory) -> None:
    dialogue, session = dialogue_session(uuid_factory)
    full = BookingPatch(
        name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"
    )
    dialogue.handle(session, ExtractionResult(patch=full), NOW)
    dialogue.mark_playback_finished(session, session.confirmation_response_id)
    corrected = dialogue.handle(
        session,
        ExtractionResult(
            patch=BookingPatch(phone="0912345678"), intent="affirm"
        ),
        NOW,
    )
    assert corrected.kind == "confirm"
    assert session.status.value == "AWAITING_CONFIRMATION"
    assert session.confirmed_revision is None


def test_cancellation_never_emits_payload(uuid_factory) -> None:
    dialogue, session = dialogue_session(uuid_factory)
    decision = dialogue.handle(session, ExtractionResult(intent="cancel"), NOW)
    assert decision.kind == "cancel" and decision.payload is None


@pytest.mark.parametrize("kind", ["clone", "archetype"])
def test_runtime_profile_preserves_selected_voice_kind(kind) -> None:
    profile = RuntimeProfile.default().model_copy(
        update={
            "voice_ref": VoiceRef(
                provider_id="voicestudio",
                kind=kind,
                provider_voice_id="voice-1",
            )
        }
    )
    assert profile.speech_config().voice_ref.kind == kind


def test_timing_separates_transmission_from_playback() -> None:
    server = TurnTiming(clock_domain="server")
    server.mark("input.end", 10.0)
    server.mark("stt.final", 10.2)
    server.mark("llm.first_text", 10.5)
    server.mark("tts.first_audio", 10.9)
    assert server.metrics()["end_to_first_audio_seconds"] == pytest.approx(0.9)

    client = TurnTiming(clock_domain="client")
    client.mark("audio.received", 500.0)
    client.mark("playback.first_audio", 500.04)
    metrics = client.metrics()
    assert metrics["playback_delay_seconds"] == pytest.approx(0.04)
    assert client.marks["playback.first_audio"] >= client.marks["audio.received"]

    recorder = TimingRecorder()
    recorder.record(server, {"model": "fake", "hardware": "test"})
    recorder.record(client, {})
    assert recorder.summary()["server"]["end_to_first_audio_seconds"]["p95"] == pytest.approx(0.9)
