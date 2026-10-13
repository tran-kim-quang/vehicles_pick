from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .confirmation import confirmation_text
from .fields import FieldRegistry
from .models import CallSession, CallStatus, ExtractionResult
from .serializer import BookingSerializer


class DialogueDecision(BaseModel):
    kind: Literal["ask", "confirm", "complete", "cancel", "assist"]
    text: str
    field: str | None = None
    payload: dict | None = None
    validation_errors: list[str] = Field(default_factory=list)


QUESTIONS = {
    "name": "Bạn tên là gì?",
    "phone": "Số điện thoại của bạn là gì?",
    "booking_mode": "Bạn muốn đi ngay hay đặt trước?",
    "scheduled_at": "Bạn muốn đặt xe lúc mấy giờ, ngày nào?",
    "vehicle_type": "Bạn muốn đi bike hay car?",
    "pickup": "Bạn muốn đón xe ở đâu?",
    "destination": "Bạn muốn đi đến đâu?",
}


class DialogueManager:
    def __init__(self, fields: FieldRegistry, serializer: BookingSerializer | None = None) -> None:
        self.fields = fields
        self.serializer = serializer or BookingSerializer(fields.timezone_name)

    def handle(self, session: CallSession, result: ExtractionResult, now: datetime) -> DialogueDecision:
        if session.status == CallStatus.COMPLETED:
            return DialogueDecision(
                kind="complete",
                text="Thông tin đã được xác nhận.",
                payload=session.final_payload,
            )
        if session.status == CallStatus.CANCELLED:
            return DialogueDecision(kind="cancel", text="Yêu cầu đã được hủy.")
        if session.status == CallStatus.NEEDS_ASSISTANCE:
            return DialogueDecision(kind="assist", text="Phiên đang chờ hỗ trợ viên.")
        before = session.revision
        errors = self.fields.apply(session, result.patch, now)
        changed = session.revision != before
        if changed:
            self.invalidate_confirmation(session)
        if result.intent == "cancel":
            session.status = CallStatus.CANCELLED
            return DialogueDecision(kind="cancel", text="Đã hủy yêu cầu.")
        if not session.vehicles_snapshot:
            session.status = CallStatus.NEEDS_ASSISTANCE
            return DialogueDecision(kind="assist", text="Hiện chưa có loại xe khả dụng.")

        missing = self.fields.missing_fields(session)
        if missing:
            field = missing[0]
            for resolved in session.attempts_by_field.keys() & session.fields.keys():
                if session.fields[resolved].valid:
                    session.attempts_by_field[resolved] = 0
            if field in errors or (not changed and result.intent != "cancel"):
                session.attempts_by_field[field] = session.attempts_by_field.get(field, 0) + 1
            session.last_asked_field = field
            if session.attempts_by_field.get(field, 0) >= 3:
                session.status = CallStatus.NEEDS_ASSISTANCE
                return DialogueDecision(kind="assist", text="Tôi sẽ chuyển bạn sang hỗ trợ viên.")
            session.status = CallStatus.COLLECTING
            text = QUESTIONS[field]
            if field == "scheduled_at" and session.schedule_proposal:
                proposal = session.schedule_proposal
                if session.schedule_error == "date and time are required":
                    if proposal.hour is not None:
                        text = f"Mình đã ghi nhận giờ {proposal.hour:02d}:{proposal.minute or 0:02d}. Bạn muốn đặt xe ngày nào trong tháng này?"
                    else:
                        text = "Bạn muốn đặt xe lúc mấy giờ, bao nhiêu phút?"
                elif session.schedule_error:
                    text = session.schedule_error + " Bạn vui lòng cung cấp lại ngày và giờ hợp lệ nhé."
            if "phone" in errors:
                text = "Số điện thoại không đúng định dạng. Bạn vui lòng cung cấp lại số điện thoại hợp lệ nhé."
                if field != "phone":
                    text += " " + QUESTIONS[field]
            return DialogueDecision(kind="ask", field=field, text=text, validation_errors=errors)

        if result.intent == "affirm" and not changed:
            if (
                session.status == CallStatus.AWAITING_CONFIRMATION
                and session.playback_finished
                and session.pending_confirmation_revision == session.revision
            ):
                if not self._revalidate_schedule(session, now):
                    return DialogueDecision(kind="ask", field="scheduled_at", text=QUESTIONS["scheduled_at"])
                session.confirmed_revision = session.revision
                session.status = CallStatus.COMPLETED
                payload = self.serializer.serialize(session, now)
                session.final_payload = payload
                return DialogueDecision(kind="complete", text="Thông tin đã được xác nhận.", payload=payload)
            return DialogueDecision(kind="ask", text="Vui lòng xác nhận sau khi nghe hết thông tin.")

        session.status = CallStatus.AWAITING_CONFIRMATION
        session.pending_confirmation_revision = session.revision
        session.confirmation_response_id = str(uuid4())
        session.playback_finished = False
        return DialogueDecision(kind="confirm", text=confirmation_text(session))

    def mark_playback_finished(self, session: CallSession, response_id: str | None) -> None:
        if response_id and response_id == session.confirmation_response_id:
            session.playback_finished = True

    @staticmethod
    def invalidate_confirmation(session: CallSession) -> None:
        session.pending_confirmation_revision = None
        session.confirmation_response_id = None
        session.playback_finished = False
        if session.status == CallStatus.AWAITING_CONFIRMATION:
            session.status = CallStatus.COLLECTING

    def _revalidate_schedule(self, session: CallSession, now: datetime) -> bool:
        if session.fields["booking_mode"].value != "scheduled":
            return True
        scheduled = session.fields.get("scheduled_at")
        if scheduled and scheduled.valid and scheduled.value > now:
            return True
        if scheduled:
            scheduled.valid = False
            session.revision += 1
        self.invalidate_confirmation(session)
        return False
