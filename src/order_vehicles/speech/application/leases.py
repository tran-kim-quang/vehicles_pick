import asyncio
from collections import Counter
from contextlib import asynccontextmanager

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import RuntimeProfile


class ResourceLeases:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._condition = asyncio.Condition()
        self._mutating = False

    @asynccontextmanager
    async def acquire(self, profile: RuntimeProfile):
        resources = self._resources(profile)
        async with self._condition:
            while self._mutating:
                await self._condition.wait()
            self._counts.update(resources)
        try:
            yield
        finally:
            async with self._condition:
                for resource in resources:
                    self._counts[resource] -= 1
                    if self._counts[resource] <= 0:
                        del self._counts[resource]
                self._condition.notify_all()

    @asynccontextmanager
    async def mutation(self):
        async with self._condition:
            if self._counts.get("global:selection", 0):
                raise SpeechError("RESOURCE_IN_USE", "Speech runtime has active sessions")
            while self._mutating:
                await self._condition.wait()
            self._mutating = True
        try:
            yield
        finally:
            async with self._condition:
                self._mutating = False
                self._condition.notify_all()

    async def assert_mutable(self, ref: str) -> None:
        async with self._condition:
            in_use = (
                bool(self._counts)
                if ref == "global:selection"
                else self._counts.get(ref, 0) > 0
            )
        if in_use:
            raise SpeechError("RESOURCE_IN_USE", f"Resource {ref!r} is in active use")

    async def active_count(self) -> int:
        async with self._condition:
            return self._counts.get("global:selection", 0)

    @staticmethod
    def _resources(profile: RuntimeProfile) -> set[str]:
        resources = {
            "global:selection",
            f"engine:{profile.stt.engine_id}:{profile.stt.model_id or ''}",
            f"engine:{profile.tts.engine_id}:{profile.tts.model_id or ''}",
        }
        if profile.voice_ref:
            resources.add(ResourceLeases.voice_resource(profile.voice_ref))
            resources.add(profile.voice_ref.encode())
        return resources

    @staticmethod
    def voice_resource(ref) -> str:
        return f"voice:{ref.provider_id}:{ref.provider_voice_id}"
