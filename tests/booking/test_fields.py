from datetime import date

from order_vehicles.booking.fields import FieldRegistry
from order_vehicles.booking.models import BookingPatch, CallStatus, ScheduleProposal
from order_vehicles.booking.vehicles import VehicleCatalog


def registry(uuid_factory):
    return FieldRegistry(uuid_factory=uuid_factory)


def test_complete_now_patch(session, now, uuid_factory) -> None:
    errors = registry(uuid_factory).apply(session, BookingPatch(name=" Nam ", phone="0901234567", booking_mode="now", vehicle_choice="car"), now)
    assert errors == []
    assert registry(uuid_factory).missing_fields(session) == []
    assert session.fields["phone"].value == "+84901234567"
    assert session.fields["name"].id != session.fields["phone"].id
    assert "scheduled_at" not in session.fields


def test_scheduled_requires_date_and_time(session, now, uuid_factory) -> None:
    errors = registry(uuid_factory).apply(session, BookingPatch(booking_mode="scheduled", schedule=ScheduleProposal(date=date(2026, 10, 13))), now)
    assert "scheduled_at" in errors
    assert "scheduled_at" in registry(uuid_factory).missing_fields(session)


def test_vehicle_alias_resolves_to_catalog(session, now, uuid_factory) -> None:
    assert VehicleCatalog(["bike", "car"]).resolve("ô tô") == "car"
    registry(uuid_factory).apply(session, BookingPatch(vehicle_choice="xe máy"), now)
    assert session.fields["vehicle_type"].value == "bike"


def test_unavailable_vehicle_remains_missing(session, now, uuid_factory) -> None:
    registry(uuid_factory).apply(session, BookingPatch(vehicle_choice="truck"), now)
    assert "vehicle_type" in registry(uuid_factory).missing_fields(session)


def test_empty_catalog_requires_assistance(uuid_factory, now) -> None:
    session = __import__("order_vehicles.booking.models", fromlist=["CallSession"]).CallSession.create([], uuid_factory=uuid_factory)
    registry(uuid_factory).apply(session, BookingPatch(vehicle_choice="car"), now)
    assert session.status == CallStatus.NEEDS_ASSISTANCE


def test_invalid_correction_invalidates_old_value(session, now, uuid_factory) -> None:
    fields = registry(uuid_factory)
    fields.apply(session, BookingPatch(phone="0901234567"), now)
    fields.apply(session, BookingPatch(phone="12"), now)
    assert session.fields["phone"].valid is False
    assert "phone" in fields.missing_fields(session)


def test_field_uuid_survives_correction(session, now, uuid_factory) -> None:
    fields = registry(uuid_factory)
    fields.apply(session, BookingPatch(name="Nam"), now)
    before = session.fields["name"].id
    fields.apply(session, BookingPatch(name="Lan"), now)
    assert session.fields["name"].id == before


def test_relative_day_uses_service_timezone(session, now, uuid_factory) -> None:
    fields = registry(uuid_factory)
    fields.apply(session, BookingPatch(booking_mode="scheduled", schedule=ScheduleProposal(relative_days=1, hour=13, period="24h")), now)
    assert session.fields["scheduled_at"].value.isoformat() == "2026-10-13T13:00:00+07:00"


def test_eight_without_period_requires_clarification(session, now, uuid_factory) -> None:
    errors = registry(uuid_factory).apply(session, BookingPatch(booking_mode="scheduled", schedule=ScheduleProposal(relative_days=1, hour=8)), now)
    assert "scheduled_at" in errors


def test_midnight_is_unambiguous_24_hour_time(session, now, uuid_factory) -> None:
    errors = registry(uuid_factory).apply(
        session,
        BookingPatch(
            booking_mode="scheduled",
            schedule=ScheduleProposal(relative_days=1, hour=0),
        ),
        now,
    )
    assert errors == []
