from collections.abc import AsyncIterator
from typing import Protocol

from .models import (
    AudioPacket,
    CloneRequest,
    EngineInfo,
    ModelInfo,
    ModelSelection,
    RuntimeProfile,
    SpeechConfig,
    TranscriptionEvent,
    VoiceFilter,
    VoiceInfo,
    VoiceRef,
)


class SpeechRecognizer(Protocol):
    async def stream_transcribe(
        self, frames: AsyncIterator[bytes], config: SpeechConfig
    ) -> AsyncIterator[TranscriptionEvent]: ...


class SpeechSynthesizer(Protocol):
    async def stream_synthesize(
        self, text: str, config: SpeechConfig
    ) -> AsyncIterator[AudioPacket]: ...


class ModelManager(Protocol):
    async def list_engines(self) -> list[EngineInfo]: ...
    async def list_models(self) -> list[ModelInfo]: ...
    async def select_model(self, selection: ModelSelection) -> dict[str, object]: ...


class VoiceLibrary(Protocol):
    async def list_voices(self, filter: VoiceFilter) -> list[VoiceInfo]: ...
    async def create_clone(self, reference: bytes, metadata: CloneRequest) -> VoiceInfo: ...
    async def delete_voice(self, ref: VoiceRef) -> None: ...


class RuntimeProfileRepository(Protocol):
    def get(self) -> RuntimeProfile: ...
    def save(self, profile: RuntimeProfile, expected_revision: int) -> RuntimeProfile: ...
