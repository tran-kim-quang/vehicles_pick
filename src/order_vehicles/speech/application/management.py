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
        async with self.leases.mutation():
            previous = self.repository.assert_revision(expected_revision)
            mutation_started = False
            try:
                for requested in (profile.stt, profile.tts):
                    mutation_started = True
                    actual = await self.models.select_model(requested)
                    self._verify_selection(requested, actual)
                validate = getattr(self.models, "validate_profile", None)
                if validate:
                    await validate(profile)
                return self.repository.save(profile, expected_revision)
            except Exception:
                for selection in (previous.stt, previous.tts):
                    if mutation_started:
                        try:
                            await self.models.select_model(selection)
                        except Exception:
                            pass
                raise

    async def delete_voice(self, ref: VoiceRef) -> None:
        async with self.leases.mutation():
            await self.leases.assert_mutable(self.leases.voice_resource(ref))
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

    @staticmethod
    def _verify_selection(requested, actual) -> None:
        actual_engine = actual.get("engine_id")
        actual_model = actual.get("model_id")
        if not actual_engine or actual_engine != requested.engine_id:
            raise SpeechError(
                "MODEL_MISMATCH",
                f"Provider selected {actual_engine!r}, requested {requested.engine_id!r}",
            )
        if not actual.get("ready", True):
            raise SpeechError("MODEL_NOT_READY", str(actual.get("missing") or "Model is not ready"))
        if requested.model_id and actual_model != requested.model_id:
            raise SpeechError(
                "MODEL_MISMATCH",
                f"Provider selected model {actual_model!r}, requested {requested.model_id!r}",
            )
