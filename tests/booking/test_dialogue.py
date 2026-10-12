from datetime import timedelta

from order_vehicles.booking.dialogue import DialogueManager
from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, CallStatus, ExtractionResult, ScheduleProposal


def manager(uuid_factory): return DialogueManager(FieldRegistry(uuid_factory=uuid_factory))


def complete_now():
    return BookingPatch(name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car")


def test_asks_only_missing_field(session, now, uuid_factory) -> None:
    decision = manager(uuid_factory).handle(session, ExtractionResult(patch=BookingPatch(name="Nam"), intent="provide"), now)
    assert decision.kind == "ask" and decision.field == "phone"


def test_affirm_before_playback_does_not_complete(session, now, uuid_factory) -> None:
    dialogue = manager(uuid_factory)
    dialogue.handle(session, ExtractionResult(patch=complete_now(), intent="provide"), now)
    early = dialogue.handle(session, ExtractionResult(intent="affirm"), now)
    assert early.kind != "complete"


def test_affirm_with_correction_requires_new_readback(session, now, uuid_factory) -> None:
    dialogue = manager(uuid_factory)
    confirm = dialogue.handle(session, ExtractionResult(patch=complete_now(), intent="provide"), now)
    dialogue.mark_playback_finished(session, session.confirmation_response_id)
    corrected = dialogue.handle(session, ExtractionResult(patch=BookingPatch(phone="0912345678"), intent="affirm"), now)
    assert corrected.kind == "confirm" and session.status == CallStatus.AWAITING_CONFIRMATION


def test_schedule_expired_before_confirmation(session, now, uuid_factory) -> None:
    dialogue = manager(uuid_factory)
    patch = BookingPatch(name="Nam", phone="0901234567", booking_mode="scheduled", schedule=ScheduleProposal(relative_days=1, hour=13, period="24h"), vehicle_choice="car")
    dialogue.handle(session, ExtractionResult(patch=patch, intent="provide"), now)
    dialogue.mark_playback_finished(session, session.confirmation_response_id)
    result = dialogue.handle(session, ExtractionResult(intent="affirm"), now + timedelta(days=2))
    assert result.kind == "ask" and result.field == "scheduled_at"


def test_third_failed_answer_requires_assistance(session, now, uuid_factory) -> None:
    dialogue = manager(uuid_factory)
    for _ in range(3):
        decision = dialogue.handle(session, ExtractionResult(intent="unknown"), now)
    assert decision.kind == "assist"


def test_cancel_never_exports(session, now, uuid_factory) -> None:
    decision = manager(uuid_factory).handle(session, ExtractionResult(patch=complete_now(), intent="cancel"), now)
    assert decision.kind == "cancel" and decision.payload is None
