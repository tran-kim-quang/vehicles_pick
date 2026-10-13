from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from order_vehicles.booking.models import CallSession, ExtractionResult
from order_vehicles.speech.domain.errors import SpeechError


class InformationExtractor:
    def __init__(
        self,
        adapter,
        *,
        timezone: str = "Asia/Ho_Chi_Minh",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.adapter = adapter
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(ZoneInfo(self.timezone)))

    async def extract(self, transcript: str, session: CallSession) -> ExtractionResult:
        context = self.context(session, now=self.clock(), timezone=self.timezone)
        error = None
        for attempt in range(2):
            try:
                raw = await self.adapter.extract(
                    transcript, context, retry=bool(attempt)
                )
                return raw if isinstance(raw, ExtractionResult) else ExtractionResult.model_validate(raw)
            except (ValidationError, ValueError, KeyError) as exc:
                error = exc
        raise SpeechError("LLM_SCHEMA", "LLM returned invalid structured data", True) from error

    @staticmethod
    def context(
        session: CallSession,
        *,
        now: datetime | None = None,
        timezone: str = "Asia/Ho_Chi_Minh",
    ) -> dict:
        fields = {name: field.value for name, field in session.fields.items() if field.valid}
        current = now or datetime.now(ZoneInfo(timezone))
        return {
            "vehicles_available": list(session.vehicles_snapshot),
            "validated_fields": fields,
            "pending_question": session.last_asked_field,
            "partial_schedule": session.schedule_proposal.model_dump(mode="json") if session.schedule_proposal else None,
            "service_timestamp": current.isoformat(),
            "timezone": timezone,
        }
