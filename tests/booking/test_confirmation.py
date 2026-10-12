from order_vehicles.booking.confirmation import confirmation_text
from order_vehicles.booking.dialogue import DialogueManager
from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, ExtractionResult


def test_confirmation_revision_must_match(session, now, uuid_factory) -> None:
    dialogue = DialogueManager(FieldRegistry(uuid_factory=uuid_factory))
    dialogue.handle(session, ExtractionResult(patch=BookingPatch(name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"), intent="provide"), now)
    dialogue.mark_playback_finished(session, session.confirmation_response_id)
    session.pending_confirmation_revision -= 1
    assert dialogue.handle(session, ExtractionResult(intent="affirm"), now).kind != "complete"


def test_readback_comes_from_validated_fields(session, now, uuid_factory) -> None:
    fields = FieldRegistry(uuid_factory=uuid_factory)
    fields.apply(session, BookingPatch(name="Nam", phone="0901234567", booking_mode="now", vehicle_choice="car"), now)
    text = confirmation_text(session)
    assert "Nam" in text and "0 9 0 1 2 3 4 5 6 7" in text and "car" in text
