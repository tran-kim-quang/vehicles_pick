from datetime import datetime
from zoneinfo import ZoneInfo

from order_vehicles.speech.domain.errors import SpeechError

from .models import CallSession, CallStatus


class BookingSerializer:
    def __init__(self, timezone_name: str = "Asia/Ho_Chi_Minh") -> None:
        self.timezone_name = timezone_name
        self._emitted: set[tuple[object, int]] = set()

    def serialize(self, session: CallSession, now: datetime) -> dict:
        if session.status != CallStatus.COMPLETED or session.confirmed_revision is None:
            raise SpeechError("NOT_CONFIRMED", "Booking has not been confirmed")
        key = (session.request_id, session.confirmed_revision)
        if key in self._emitted or session.final_emitted:
            raise SpeechError("ALREADY_EMITTED", "Confirmed booking was already emitted")
        fields = {}
        for name, field in session.fields.items():
            if not field.valid or (name == "scheduled_at" and session.fields["booking_mode"].value == "now"):
                continue
            value = field.value.isoformat() if isinstance(field.value, datetime) else field.value
            fields[name] = {"id": str(field.id), "value": value}
        payload = {
            "schema_version": "1.0",
            "request_id": str(session.request_id),
            "status": "confirmed",
            "revision": session.revision,
            "confirmed_revision": session.confirmed_revision,
            "timezone": self.timezone_name,
            "booking_at": (
                session.fields["scheduled_at"].value if session.fields["booking_mode"].value == "scheduled"
                else now.astimezone(ZoneInfo(self.timezone_name))
            ).isoformat(),
            "fields": fields,
        }
        self._emitted.add(key)
        session.final_emitted = True
        return payload
