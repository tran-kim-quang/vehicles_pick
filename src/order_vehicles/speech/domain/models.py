import base64
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AudioFormat(ContractModel):
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: int = Field(gt=0)
    channels: int = Field(default=1, gt=0)


class AudioPacket(ContractModel):
    format: AudioFormat
    data: bytes


class TranscriptionEvent(ContractModel):
    kind: Literal["partial", "final"]
    text: str
    final_kind: Literal["utterance", "summary"] | None = None
    actual_engine: str | None = None


class ModelSelection(ContractModel):
    engine_id: str
    model_id: str | None = None
    purpose: Literal["stt_stream", "stt_batch", "tts"]


class VoiceRef(ContractModel):
    provider_id: str
    kind: Literal["clone", "design", "preset", "default"]
    provider_voice_id: str

    def encode(self) -> str:
        raw = json.dumps(
            self.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, value: str) -> "VoiceRef":
        padding = "=" * (-len(value) % 4)
        try:
            payload = base64.urlsafe_b64decode(value + padding)
            return cls.model_validate_json(payload)
        except Exception as exc:
            raise ValueError("invalid provider-scoped voice reference") from exc


class SpeechConfig(ContractModel):
    stt: ModelSelection
    tts: ModelSelection
    voice_ref: VoiceRef | None = None
    language: str = "vi"
    speed: float = Field(default=1.0, gt=0)
    exact_model: bool = False


class EngineInfo(ContractModel):
    id: str
    family: Literal["asr", "tts"]
    available: bool | None = None
    selected: bool | None = None
    capabilities: dict[str, bool | None] = Field(default_factory=dict)


class ModelInfo(ContractModel):
    repo_id: str
    family: Literal["asr", "tts"] | None = None
    installation_state: Literal["available", "incomplete", "installed", "unknown"] = "unknown"
    loaded: bool | None = None
    engine_ids: tuple[str, ...] = ()


class VoiceInfo(ContractModel):
    reference: VoiceRef
    name: str
    language: str | None = None
    description: str | None = None
    default_alias: bool = False
    capabilities: dict[str, bool | None] = Field(default_factory=dict)


class VoiceFilter(ContractModel):
    language: str | None = None
    kind: str | None = None
    query: str | None = None


class CloneRequest(ContractModel):
    name: str
    ref_text: str = ""
    language: str = "vi"
    filename: str = "reference.wav"
    description: str | None = None


class RuntimeProfile(ContractModel):
    revision: int = Field(default=1, ge=1)
    stt: ModelSelection
    tts: ModelSelection
    voice_ref: VoiceRef | None = None
    language: str = "vi"
    speed: float = 1.0
    exact_model: bool = False

    @classmethod
    def default(cls) -> "RuntimeProfile":
        return cls(
            stt=ModelSelection(engine_id="capture", purpose="stt_stream"),
            tts=ModelSelection(engine_id="omnivoice", purpose="tts"),
        )

    def speech_config(self) -> SpeechConfig:
        return SpeechConfig(**self.model_dump(exclude={"revision"}))


class ManagementJob(ContractModel):
    id: UUID
    repo_id: str
    status: Literal[
        "pending", "running", "cancel_requested", "completed", "failed", "unknown"
    ]
    progress: float | None = None
    message: str | None = None
    created_at: datetime
    updated_at: datetime


JsonObject = dict[str, Any]
