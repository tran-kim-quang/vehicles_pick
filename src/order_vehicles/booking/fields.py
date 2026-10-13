from datetime import datetime
from uuid import uuid4

import phonenumbers

from .models import BookingPatch, CallSession, CallStatus, FieldValue, ScheduleProposal
from .validators import normalize_name, normalize_phone, resolve_schedule
from .vehicles import VehicleCatalog


class FieldRegistry:
    ORDER = ("name", "phone", "booking_mode", "scheduled_at", "pickup", "destination", "vehicle_type")

    def __init__(self, *, uuid_factory=uuid4, timezone_name: str = "Asia/Ho_Chi_Minh") -> None:
        self.uuid_factory = uuid_factory
        self.timezone_name = timezone_name

    def apply(self, session: CallSession, patch: BookingPatch, now: datetime) -> list[str]:
        errors: list[str] = []
        updates = {
            "name": patch.name,
            "phone": patch.phone,
            "booking_mode": patch.booking_mode,
            "vehicle_type": patch.vehicle_choice,
            "pickup": patch.pickup,
            "destination": patch.destination,
        }
        if patch.schedule is not None:
            prior = session.schedule_proposal.model_dump() if session.schedule_proposal else {}
            incoming = patch.schedule.model_dump(exclude_none=True)
            if any(key in incoming for key in ("date", "relative_days", "day")):
                for key in ("date", "relative_days", "day", "month", "year"):
                    prior.pop(key, None)
            if "hour" in incoming:
                prior.pop("period", None)
                if "minute" not in incoming:
                    prior["minute"] = 0
            proposal = ScheduleProposal(**(prior | incoming))
            if proposal != session.schedule_proposal:
                session.schedule_proposal = proposal
                session.revision += 1
            updates["scheduled_at"] = proposal
        for field, candidate in updates.items():
            if candidate is None:
                continue
            try:
                value = self._validate(field, candidate, session, now)
            except (ValueError, phonenumbers.NumberParseException) as exc:
                self._invalidate(session, field, candidate)
                if field == "scheduled_at":
                    session.schedule_error = str(exc)
                errors.append(field)
            else:
                self._set(session, field, value)
                if field == "scheduled_at":
                    session.schedule_error = None
        if patch.booking_mode == "now" and "scheduled_at" in session.fields:
            del session.fields["scheduled_at"]
            session.revision += 1
        if patch.booking_mode == "now":
            session.schedule_proposal = None
            session.schedule_error = None
        if not session.vehicles_snapshot:
            session.status = CallStatus.NEEDS_ASSISTANCE
        return errors

    def missing_fields(self, session: CallSession) -> list[str]:
        if not session.vehicles_snapshot:
            return ["vehicle_type"]
        result = []
        for name in self.ORDER:
            if name in {"pickup", "destination"} and not ({"pickup", "destination"} & session.fields.keys()):
                continue
            if name == "scheduled_at" and self._value(session, "booking_mode") != "scheduled":
                continue
            if name not in session.fields or not session.fields[name].valid:
                result.append(name)
        return result

    def _validate(self, field, candidate, session, now):
        if field in {"name", "pickup", "destination"}:
            return normalize_name(candidate)
        if field == "phone":
            return normalize_phone(candidate)
        if field == "booking_mode":
            if candidate not in {"now", "scheduled"}:
                raise ValueError
            return candidate
        if field == "vehicle_type":
            result = VehicleCatalog(session.vehicles_snapshot).resolve(candidate)
            if result is None:
                raise ValueError
            return result
        if field == "scheduled_at":
            return resolve_schedule(candidate, now, self.timezone_name)
        raise ValueError

    def _set(self, session, name, value):
        current = session.fields.get(name)
        if current and current.valid and current.value == value:
            return
        field_id = session.field_ids.setdefault(name, self.uuid_factory())
        session.fields[name] = FieldValue(id=field_id, value=value, valid=True)
        session.revision += 1

    def _invalidate(self, session, name, candidate):
        field_id = session.field_ids.get(name)
        if field_id is None:
            return
        current = session.fields.get(name)
        if not current or current.valid or current.value != candidate:
            session.fields[name] = FieldValue(id=field_id, value=candidate, valid=False)
            session.revision += 1

    @staticmethod
    def _value(session, name):
        field = session.fields.get(name)
        return field.value if field and field.valid else None
