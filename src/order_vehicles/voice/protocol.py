from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputAudioFormat(ProtocolModel):
    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16000]
    channels: Literal[1]


class SessionStart(ProtocolModel):
    type: Literal["session.start"]
    version: Literal["voice.booking.v1"]
    audio_format: InputAudioFormat
    profile_ref: str = ""


class SessionResume(ProtocolModel):
    type: Literal["session.resume"]
    version: Literal["voice.booking.v1"]
    session_id: UUID
    resume_token: str = Field(min_length=1)
    audio_format: InputAudioFormat


class InputAudioStart(ProtocolModel):
    type: Literal["input_audio.start"]
    session_id: UUID | None = None
    turn_id: str = Field(min_length=1)


class InputAudioEnd(ProtocolModel):
    type: Literal["input_audio.end"]
    session_id: UUID | None = None
    turn_id: str = Field(min_length=1)


class InputAudioAbort(ProtocolModel):
    type: Literal["input_audio.abort"]
    session_id: UUID | None = None
    turn_id: str = Field(min_length=1)
    reason: Literal["overflow", "cancelled"] = "cancelled"


class ResponseInterrupt(ProtocolModel):
    type: Literal["response.interrupt"]
    session_id: UUID | None = None


class PlaybackFinished(ProtocolModel):
    type: Literal["assistant.playback.finished"]
    session_id: UUID | None = None
    response_id: str = Field(min_length=1)


ClientEvent = Annotated[
    SessionStart
    | SessionResume
    | InputAudioStart
    | InputAudioEnd
    | InputAudioAbort
    | ResponseInterrupt
    | PlaybackFinished,
    Field(discriminator="type"),
]
CLIENT_EVENT_ADAPTER = TypeAdapter(ClientEvent)


def parse_client_event(value: object) -> ClientEvent:
    return CLIENT_EVENT_ADAPTER.validate_python(value)
