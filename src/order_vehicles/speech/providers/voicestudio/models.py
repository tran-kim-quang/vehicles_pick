from collections.abc import AsyncIterator, Iterable
from typing import Any

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import EngineInfo, ModelInfo, ModelSelection

from .client import VoiceStudioClient


class VoiceStudioModelManager:
    def __init__(
        self,
        client: VoiceStudioClient,
        *,
        known_models: Iterable[str] | None = None,
        engines_with_model_selection: Iterable[str] = ("mlx-audio", "faster-whisper"),
    ) -> None:
        self.client = client
        self.known_models = frozenset(known_models) if known_models is not None else None
        self.engines_with_model_selection = frozenset(engines_with_model_selection)

    async def list_engines(self) -> list[EngineInfo]:
        result: list[EngineInfo] = []
        for family in ("asr", "tts"):
            response = await self.client.request("GET", f"/engines/{family}")
            payload = response.json()
            items = payload.get("backends", payload.get("engines", payload if isinstance(payload, list) else []))
            active = payload.get("active") if isinstance(payload, dict) else None
            active_model = payload.get("active_model") if isinstance(payload, dict) else None
            env_override = bool(payload.get("env_override")) if isinstance(payload, dict) else False
            for item in items:
                capabilities = dict(item.get("capabilities", {}))
                if "supports_cloning" in item:
                    capabilities["voice_clone"] = item.get("supports_cloning")
                result.append(
                    EngineInfo(
                        id=str(item.get("id") or item.get("backend_id")),
                        family=family,
                        available=item.get("available"),
                        selected=item.get("selected", item.get("id") == active),
                        capabilities=capabilities,
                        active_model=active_model if item.get("id") == active else None,
                        env_override=env_override,
                    )
                )
        return result

    async def list_models(self) -> list[ModelInfo]:
        response = await self.client.request("GET", "/models")
        payload = response.json()
        items = payload.get("models", payload if isinstance(payload, list) else [])
        return [self._model(item) for item in items]

    async def install_model(self, repo_id: str) -> str:
        allowed = self.known_models
        if allowed is None:
            allowed = frozenset(
                item.repo_id for item in await self.list_models() if item.supported is not False
            )
        if repo_id not in allowed:
            raise SpeechError("UNKNOWN_MODEL", f"Model {repo_id!r} is not in the supported catalog")
        await self.client.request(
            "POST", "/models/install", json={"repo_id": repo_id, "target": "local"}
        )
        return repo_id

    async def installation_status(self) -> list[dict[str, Any]]:
        response = await self.client.request("GET", "/models/install/status")
        payload = response.json()
        return payload if isinstance(payload, list) else payload.get("jobs", payload.get("items", []))

    async def watch_installation(self) -> AsyncIterator[dict[str, Any]]:
        async for event in self.client.iter_sse("/setup/download-stream"):
            yield event

    async def cancel_install(self, repo_id: str) -> None:
        await self.client.request(
            "POST", "/models/install/cancel", json={"repo_id": repo_id}
        )

    async def select_model(self, selection: ModelSelection) -> dict[str, Any]:
        if selection.purpose == "stt_stream":
            if selection.engine_id != "capture":
                raise SpeechError("UNSUPPORTED_SELECTION", "Streaming STT uses the capture engine")
            if selection.model_id:
                await self.client.request("POST", "/dictation/prefs", json={"model_id": selection.model_id})
            prefs = (await self.client.request("GET", "/dictation/prefs")).json()
            readiness = (
                await self.client.request(
                    "GET",
                    "/dictation/readiness",
                    params={"model_id": selection.model_id or prefs.get("model_id"), "purpose": "dictation"},
                )
            ).json()
            return {
                "engine_id": "capture",
                "model_id": prefs.get("model_id"),
                "ready": bool(readiness.get("ready")),
                "missing": readiness.get("missing"),
                "env_override": False,
            }
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
        return {
            "engine_id": payload.get("active"),
            "model_id": payload.get("active_model"),
            "ready": any(
                item.get("id") == payload.get("active") and item.get("available") is not False
                for item in payload.get("backends", [])
            ),
            "env_override": bool(payload.get("env_override")),
        }

    async def validate_profile(self, profile) -> None:
        readiness = (
            await self.client.request(
                "GET",
                "/dictation/readiness",
                params={"model_id": profile.stt.model_id, "purpose": "dictation"},
            )
        ).json()
        if not readiness.get("ready"):
            raise SpeechError("MODEL_NOT_READY", str(readiness.get("missing") or "Streaming STT is not ready"))
        payload = (await self.client.request("GET", "/engines/tts")).json()
        if payload.get("active") != profile.tts.engine_id:
            raise SpeechError("MODEL_MISMATCH", "Active TTS engine does not match the profile")
        if profile.tts.model_id and payload.get("active_model") != profile.tts.model_id:
            raise SpeechError("MODEL_MISMATCH", "Active TTS model does not match the profile")
        active = next((item for item in payload.get("backends", []) if item.get("id") == payload.get("active")), None)
        if not active or active.get("available") is False:
            raise SpeechError("MODEL_NOT_READY", "Active TTS engine is not available")

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
            supported=item.get("supported"),
        )
