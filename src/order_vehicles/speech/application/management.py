import asyncio
from typing import Any

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import RuntimeProfile, VoiceRef

from .leases import ResourceLeases
from .profiles import JsonRuntimeProfileRepository


class SpeechManagementService:
    def __init__(
        self,
        repository: JsonRuntimeProfileRepository,
        leases: ResourceLeases,
        models: Any,
        voices: Any,
        *,
        preview_concurrency: int = 1,
    ) -> None:
        self.repository = repository
        self.leases = leases
        self.models = models
        self.voices = voices
        self._preview_limit = asyncio.Semaphore(preview_concurrency)

    async def update_profile(
        self, profile: RuntimeProfile, expected_revision: int
    ) -> RuntimeProfile:
        await self.leases.assert_mutable("global:selection")
        for requested in (profile.stt, profile.tts):
            actual = await self.models.select_model(requested)
            actual_engine = actual.get("backend_id") or actual.get("engine_id")
            actual_model = actual.get("model_id")
            if actual_engine and actual_engine != requested.engine_id:
                raise SpeechError(
                    "MODEL_MISMATCH",
                    f"Provider selected {actual_engine!r}, requested {requested.engine_id!r}",
                )
            if requested.model_id and actual_model and actual_model != requested.model_id:
                raise SpeechError(
                    "MODEL_MISMATCH",
                    f"Provider selected model {actual_model!r}, requested {requested.model_id!r}",
                )
        return self.repository.save(profile, expected_revision)

    async def delete_voice(self, ref: VoiceRef) -> None:
        await self.leases.assert_mutable(ref.encode())
        await self.voices.delete_voice(ref)

    async def preview_voice(self, ref: VoiceRef, text: str, config):
        profile = RuntimeProfile(
            revision=1,
            stt=config.stt,
            tts=config.tts,
            voice_ref=ref,
            language=config.language,
            speed=config.speed,
            exact_model=config.exact_model,
        )
        async with self._preview_limit:
            async with self.leases.acquire(profile):
                async for packet in self.voices.preview_voice(ref, text, config):
                    yield packet
