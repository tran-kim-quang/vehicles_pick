from datetime import date as Date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldValue(DomainModel):
    id: UUID
    value: object
    valid: bool = True


class ScheduleProposal(DomainModel):
    date: Date | None = None
    relative_days: int | None = Field(default=None, ge=0)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    period: Literal["am", "pm", "24h"] | None = None


class BookingPatch(DomainModel):
    name: str | None = None
    phone: str | None = None
    booking_mode: Literal["now", "scheduled"] | None = None
    schedule: ScheduleProposal | None = None
    vehicle_choice: str | None = None


class ExtractionResult(DomainModel):
    patch: BookingPatch = Field(default_factory=BookingPatch)
    intent: Literal["provide", "correct", "affirm", "deny", "cancel", "unknown"] = "unknown"


class CallStatus(StrEnum):
    COLLECTING = "COLLECTING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NEEDS_ASSISTANCE = "NEEDS_ASSISTANCE"


class CallSession(DomainModel):
    session_id: UUID
    request_id: UUID
    profile_ref: str = ""
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    field_ids: dict[str, UUID] = Field(default_factory=dict)
    vehicles_snapshot: tuple[str, ...]
    revision: int = 0
    status: CallStatus = CallStatus.COLLECTING
    pending_confirmation_revision: int | None = None
    confirmed_revision: int | None = None
    confirmation_response_id: str | None = None
    playback_finished: bool = False
    attempts_by_field: dict[str, int] = Field(default_factory=dict)
    last_asked_field: str | None = None
    final_emitted: bool = False
    detached_at: datetime | None = None
    resume_token: str | None = None

    @classmethod
    def create(cls, vehicles: list[str], *, uuid_factory=uuid4, profile_ref: str = "") -> "CallSession":
        return cls(
            session_id=uuid_factory(),
            request_id=uuid_factory(),
            vehicles_snapshot=tuple(vehicles),
            profile_ref=profile_ref,
        )
