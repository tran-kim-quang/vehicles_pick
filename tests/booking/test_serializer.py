from datetime import timedelta

import pytest

from order_vehicles.booking.dialogue import DialogueManager
from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, ExtractionResult
from order_vehicles.booking.repository import SessionRepository
from order_vehicles.booking.serializer import BookingSerializer
from order_vehicles.speech.domain.errors import SpeechError


def completed(session, now, uuid_factory):
    serializer = BookingSerializer()
    dialogue = DialogueManager(FieldRegistry(uuid_factory=uuid_factory), serializer)
    dialogue.handle(session, ExtractionResult(patch=BookingPatch(name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"), intent="provide"), now)
    dialogue.mark_playback_finished(session, session.confirmation_response_id)
    decision = dialogue.handle(session, ExtractionResult(intent="affirm"), now)
    return session, serializer, decision.payload


def test_serializer_emits_once(session, now, uuid_factory) -> None:
    session, serializer, payload = completed(session, now, uuid_factory)
    assert payload["revision"] == payload["confirmed_revision"]
    assert all({"id", "value"} == set(value) for value in payload["fields"].values())
    with pytest.raises(SpeechError): serializer.serialize(session, now)


def test_now_omits_scheduled_at(session, now, uuid_factory) -> None:
    _, _, payload = completed(session, now, uuid_factory)
    assert "scheduled_at" not in payload["fields"]
    assert payload["booking_at"] == now.isoformat()


def test_repository_expires_draft_after_15_minutes(now, uuid_factory) -> None:
    repository = SessionRepository(["bike", "car"], uuid_factory=uuid_factory)
    session = repository.create("profile")
    repository.detach(session.session_id, now)
    repository.expire(now + timedelta(seconds=901))
    with pytest.raises(SpeechError): repository.get(session.session_id)


def test_repository_rejects_expired_session_on_read(now, uuid_factory) -> None:
    repository = SessionRepository(["car"], uuid_factory=uuid_factory)
    session = repository.create("")
    repository.detach(session.session_id, now)
    with pytest.raises(SpeechError) as caught:
        repository.get(session.session_id, now + timedelta(minutes=16))
    assert caught.value.code == "SESSION_EXPIRED"
