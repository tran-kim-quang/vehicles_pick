import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.greeting import GREETING_TEXT
from order_vehicles.speech.domain.models import (
    AudioPacket,
    RuntimeProfile,
    TranscriptionEvent,
)

from .leases import ResourceLeases
from .profiles import JsonRuntimeProfileRepository


@dataclass(slots=True)
class SpeechRuntime:
    profile: RuntimeProfile
    recognizer: Any
    synthesizer: Any
    greeting_audio: AudioPacket | None = None
    _active_streams: set[Any] = field(default_factory=set)

    async def transcribe(self, frames) -> AsyncIterator[TranscriptionEvent]:
        stream = self.recognizer.stream_transcribe(frames, self.profile.speech_config())
        self._active_streams.add(stream)
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()
            self._active_streams.discard(stream)

    async def speak(self, text: str) -> AsyncIterator[AudioPacket]:
        if text == GREETING_TEXT and self.greeting_audio is not None:
            yield self.greeting_audio
            return
        stream = self.synthesizer.stream_synthesize(text, self.profile.speech_config())
        self._active_streams.add(stream)
        try:
            async for packet in stream:
                yield packet
        finally:
            await stream.aclose()
            self._active_streams.discard(stream)

    async def aclose(self) -> None:
        streams = list(self._active_streams)
        for stream in streams:
            await stream.aclose()
        self._active_streams.clear()


class SpeechRuntimeService:
    def __init__(
        self,
        repository: JsonRuntimeProfileRepository,
        leases: ResourceLeases,
        providers: dict[str, tuple[Any, Any]],
        *,
        readiness: Callable[[RuntimeProfile], bool | Awaitable[bool]],
        profile_overrides: dict | None = None,
        greeting_audio: AudioPacket | None = None,
    ) -> None:
        self.repository = repository
        self.leases = leases
        self.providers = providers
        self.readiness = readiness
        self.profile_overrides = profile_overrides or {}
        self.greeting_audio = greeting_audio

    @asynccontextmanager
    async def open(self, profile: RuntimeProfile | None = None):
        snapshot = (profile or self.repository.get()).model_copy(deep=True)
        snapshot = snapshot.model_copy(update=self.profile_overrides)
        ready = self.readiness(snapshot)
        if inspect.isawaitable(ready):
            ready = await ready
        if not ready:
            raise SpeechError(
                "MODEL_NOT_READY", "Configured speech model weights are not ready"
            )
        provider_id = (
            snapshot.voice_ref.provider_id if snapshot.voice_ref else "voicestudio"
        )
        try:
            recognizer, synthesizer = self.providers[provider_id]
        except KeyError as exc:
            raise SpeechError(
                "UNKNOWN_PROVIDER", f"No speech provider registered for {provider_id!r}"
            ) from exc
        async with self.leases.acquire(snapshot):
            runtime = SpeechRuntime(snapshot, recognizer, synthesizer, greeting_audio=self.greeting_audio)
            try:
                yield runtime
            finally:
                await runtime.aclose()
