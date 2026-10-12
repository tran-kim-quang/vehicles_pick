from collections.abc import AsyncIterator, Iterable
from typing import Any

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import EngineInfo, ModelInfo, ModelSelection

from .client import VoiceStudioClient


DEFAULT_KNOWN_MODELS = frozenset(
    {
        "k2-fsa/OmniVoice",
        "Systran/faster-whisper-small",
        "Systran/faster-whisper-medium",
    }
)


class VoiceStudioModelManager:
    def __init__(
        self,
        client: VoiceStudioClient,
        *,
        known_models: Iterable[str] = DEFAULT_KNOWN_MODELS,
        engines_with_model_selection: Iterable[str] = ("mlx-audio", "faster-whisper"),
    ) -> None:
        self.client = client
        self.known_models = frozenset(known_models)
        self.engines_with_model_selection = frozenset(engines_with_model_selection)

    async def list_engines(self) -> list[EngineInfo]:
        result: list[EngineInfo] = []
        for family in ("asr", "tts"):
            response = await self.client.request("GET", f"/engines/{family}")
            payload = response.json()
            items = payload.get("engines", payload if isinstance(payload, list) else [])
            for item in items:
                result.append(
                    EngineInfo(
                        id=str(item.get("id") or item.get("backend_id")),
                        family=family,
                        available=item.get("available"),
                        selected=item.get("selected"),
                        capabilities=item.get("capabilities", {}),
                    )
                )
        return result

    async def list_models(self) -> list[ModelInfo]:
        response = await self.client.request("GET", "/models")
        payload = response.json()
        items = payload.get("models", payload if isinstance(payload, list) else [])
        return [self._model(item) for item in items]

    async def install_model(self, repo_id: str) -> str:
        if repo_id not in self.known_models:
            raise SpeechError("UNKNOWN_MODEL", f"Model {repo_id!r} is not in the supported catalog")
        await self.client.request(
            "POST", "/models/install", json={"repo_id": repo_id, "target": "local"}
        )
        return repo_id

    async def installation_status(self) -> list[dict[str, Any]]:
        response = await self.client.request("GET", "/models/install/status")
        payload = response.json()
        return payload if isinstance(payload, list) else payload.get("items", [])

    async def watch_installation(self) -> AsyncIterator[dict[str, Any]]:
        async for event in self.client.iter_sse("/setup/download-stream"):
            yield event

    async def cancel_install(self, repo_id: str) -> None:
        await self.client.request(
            "POST", "/models/install/cancel", json={"repo_id": repo_id}
        )

    async def select_model(self, selection: ModelSelection) -> dict[str, Any]:
        family = "tts" if selection.purpose == "tts" else "asr"
        body: dict[str, Any] = {"family": family, "backend_id": selection.engine_id}
        if (
            selection.model_id
            and selection.engine_id in self.engines_with_model_selection
        ):
            body["model_id"] = selection.model_id
        await self.client.request("POST", "/engines/select", json=body)
        response = await self.client.request("GET", f"/engines/{family}")
        payload = response.json()
        return payload.get("selected", payload)

    @staticmethod
    def _model(item: dict[str, Any]) -> ModelInfo:
        if item.get("incomplete"):
            state = "incomplete"
        elif item.get("installed") and item.get("complete", True):
            state = "installed"
        else:
            state = "available"
        return ModelInfo(
            repo_id=str(item.get("repo_id") or item.get("id")),
            family=item.get("family"),
            installation_state=state,
            loaded=item.get("loaded"),
            engine_ids=tuple(item.get("engine_ids", ())),
        )
