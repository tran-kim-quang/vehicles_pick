import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from order_vehicles.app import create_app
from order_vehicles.settings import Settings
from order_vehicles.speech.application.jobs import ModelJobService
from order_vehicles.speech.application.leases import ResourceLeases
from order_vehicles.speech.application.management import SpeechManagementService
from order_vehicles.speech.application.profiles import JsonRuntimeProfileRepository
from order_vehicles.speech.application.runtime import SpeechRuntimeService
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import (
    AudioFormat,
    AudioPacket,
    ModelInfo,
    ModelSelection,
    RuntimeProfile,
    SpeechConfig,
    VoiceFilter,
    VoiceRef,
)
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.models import VoiceStudioModelManager
from order_vehicles.speech.providers.voicestudio.tts import VoiceStudioTTSAdapter
from order_vehicles.speech.providers.voicestudio.voices import VoiceStudioVoiceLibrary
from tests.speech.fakes import ScriptedConnector


def settings(tmp_path, **kwargs):
    return Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o", **kwargs)


@pytest.mark.asyncio
async def test_real_engine_payload_maps_active_model_and_clone_capability(tmp_path) -> None:
    def handler(request: httpx.Request):
        family = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "active": "omnivoice" if family == "tts" else "faster-whisper",
            "active_model": "k2-fsa/OmniVoice" if family == "tts" else "whisper-small",
            "env_override": False,
            "backends": [{"id": "omnivoice" if family == "tts" else "faster-whisper", "available": True, "supports_cloning": family == "tts"}],
        })
    client = VoiceStudioClient(settings(tmp_path), transport=httpx.MockTransport(handler))

    engines = await VoiceStudioModelManager(client).list_engines()
    await client.aclose()

    omni = next(engine for engine in engines if engine.id == "omnivoice")
    assert omni.selected is True
    assert omni.capabilities["voice_clone"] is True


@pytest.mark.asyncio
async def test_real_env_override_readback_blocks_profile_save(tmp_path) -> None:
    def handler(request: httpx.Request):
        if request.url.path == "/dictation/prefs":
            return httpx.Response(200, json={"model_id": None})
        if request.url.path == "/dictation/readiness":
            return httpx.Response(200, json={"ready": True, "missing": None})
        if request.method == "POST":
            return httpx.Response(200, json={"saved": True})
        family = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"active": "other", "active_model": None, "env_override": True, "backends": []})
    client = VoiceStudioClient(settings(tmp_path), transport=httpx.MockTransport(handler))
    models = VoiceStudioModelManager(client)
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    service = SpeechManagementService(repository, ResourceLeases(), models, voices=None)

    with pytest.raises(SpeechError) as caught:
        await service.update_profile(repository.get(), 1)
    await client.aclose()

    assert caught.value.code == "MODEL_MISMATCH"
    assert repository.get().revision == 1


@pytest.mark.asyncio
async def test_capture_selection_uses_dictation_contract(tmp_path) -> None:
    paths = []
    def handler(request: httpx.Request):
        paths.append((request.method, request.url.path))
        if request.url.path == "/dictation/prefs":
            return httpx.Response(200, json={"enabled": True, "mode": "offline", "model_id": "sherpa-vi", "prompt": ""})
        if request.url.path == "/dictation/readiness":
            return httpx.Response(200, json={"ready": True, "missing": None})
        return httpx.Response(500)
    client = VoiceStudioClient(settings(tmp_path), transport=httpx.MockTransport(handler))
    manager = VoiceStudioModelManager(client)

    actual = await manager.select_model(ModelSelection(engine_id="capture", model_id="sherpa-vi", purpose="stt_stream"))
    await client.aclose()

    assert actual["model_id"] == "sherpa-vi" and actual["ready"] is True
    assert ("POST", "/engines/select") not in paths


@pytest.mark.asyncio
async def test_real_profile_list_update_and_alias_shapes(tmp_path) -> None:
    seen = []
    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/profiles" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "abcd1234", "name": "Nam", "kind": "clone"}])
        if request.url.path == "/v1/audio/voices":
            return httpx.Response(200, json={"voices": [
                {"voice_id": "alloy", "name": "Alloy", "type": "openai_alias"},
                {"voice_id": "abcd1234", "name": "Nam", "type": "profile"},
            ]})
        return httpx.Response(200, json={"id": "abcd1234", "name": "Nam mới", "kind": "clone"})
    client = VoiceStudioClient(settings(tmp_path), transport=httpx.MockTransport(handler))
    async def capable(_): return True
    library = VoiceStudioVoiceLibrary(client, None, clone_capability=capable)

    voices = await library.list_voices(VoiceFilter())
    await library.update_voice(VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234"), {"name": "Nam mới"})
    await client.aclose()

    assert [(voice.reference.kind, voice.reference.provider_voice_id) for voice in voices] == [("clone", "abcd1234"), ("default", "alloy")]
    assert ("PUT", "/profiles/abcd1234") in seen


class RealEventManager:
    async def install_model(self, repo_id): return repo_id
    async def watch_installation(self):
        yield {"repo_id": "known/model", "phase": "install_done", "pct": 1.0}
        await asyncio.Event().wait()
    async def list_models(self): return [ModelInfo(repo_id="known/model", installation_state="installed")]
    async def cancel_install(self, repo_id): pass


@pytest.mark.asyncio
async def test_real_install_done_event_terminates_without_timeout() -> None:
    service = ModelJobService(RealEventManager(), tracking_timeout_seconds=0.2)
    job = await service.start("known/model")
    completed = await service.wait(job.id)
    assert completed.status == "completed" and completed.progress == 1.0


@pytest.mark.asyncio
async def test_tracking_refresh_failure_gets_explicit_terminal_state() -> None:
    class Broken(RealEventManager):
        async def watch_installation(self):
            raise ConnectionError("lost")
            yield
        async def list_models(self):
            raise ConnectionError("still lost")
    service = ModelJobService(Broken(), tracking_timeout_seconds=0.1)
    job = await service.start("known/model")
    ended = await service.wait(job.id)
    assert ended.status in {"failed", "unknown"}


@pytest.mark.asyncio
async def test_stale_profile_revision_has_no_upstream_side_effect(tmp_path) -> None:
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    repository.save(repository.get(), 1)
    class Models:
        calls = 0
        async def select_model(self, selection):
            self.calls += 1
            return {"engine_id": selection.engine_id, "model_id": selection.model_id, "ready": True}
        async def validate_profile(self, profile): return None
    models = Models()
    service = SpeechManagementService(repository, ResourceLeases(), models, voices=None)
    with pytest.raises(SpeechError):
        await service.update_profile(RuntimeProfile.default(), 1)
    assert models.calls == 0


@pytest.mark.asyncio
async def test_profile_mutation_reservation_blocks_new_session(tmp_path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    admitted = asyncio.Event()
    class Models:
        async def select_model(self, selection):
            entered.set()
            await release.wait()
            return {"engine_id": selection.engine_id, "model_id": selection.model_id, "ready": True}
        async def validate_profile(self, profile): return None
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    leases = ResourceLeases()
    management = SpeechManagementService(repository, leases, Models(), voices=None)
    runtime = SpeechRuntimeService(repository, leases, {"voicestudio": (object(), object())}, readiness=lambda _: True)

    update_task = asyncio.create_task(management.update_profile(repository.get(), 1))
    await entered.wait()
    async def open_session():
        async with runtime.open():
            admitted.set()
    session_task = asyncio.create_task(open_session())
    await asyncio.sleep(0)
    assert not admitted.is_set()
    release.set()
    await update_task
    await session_task


def test_preview_requires_management_auth(tmp_path) -> None:
    from tests.speech.test_api import app_client
    client, _ = app_client(tmp_path, token="admin")
    ref = VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234").encode()
    response = client.post(f"/v1/speech/voices/{ref}/preview", json={"text": "x"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_runtime_context_closes_active_nested_stream(tmp_path) -> None:
    closed = asyncio.Event()
    class Synth:
        async def stream_synthesize(self, text, config):
            try:
                yield AudioPacket(format=AudioFormat(sample_rate=24000), data=b"\0\0")
                await asyncio.Event().wait()
            finally:
                closed.set()
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    service = SpeechRuntimeService(repository, ResourceLeases(), {"voicestudio": (object(), Synth())}, readiness=lambda _: True)
    async with service.open() as runtime:
        stream = runtime.speak("x")
        await anext(stream)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_exact_tts_checks_start_engine(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16", "engine": "other"},
        b"\0\0", {"type": "done"},
    ]])
    client = VoiceStudioClient(settings(tmp_path), websocket_connector=connector)
    config = SpeechConfig(
        stt=ModelSelection(engine_id="capture", purpose="stt_stream"),
        tts=ModelSelection(engine_id="omnivoice", purpose="tts"), exact_model=True,
    )
    with pytest.raises(SpeechError) as caught:
        _ = [p async for p in VoiceStudioTTSAdapter(client).stream_synthesize("x", config)]
    assert caught.value.code == "MODEL_MISMATCH"


@pytest.mark.asyncio
async def test_saved_voice_lease_is_canonical_across_kind() -> None:
    leases = ResourceLeases()
    clone = VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="same-id")
    design = VoiceRef(provider_id="voicestudio", kind="design", provider_voice_id="same-id")
    async with leases.acquire(RuntimeProfile.default().model_copy(update={"voice_ref": clone})):
        with pytest.raises(SpeechError):
            await leases.assert_mutable(leases.voice_resource(design))


@pytest.mark.asyncio
async def test_install_accepts_model_advertised_by_live_catalog(tmp_path) -> None:
    seen = []
    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"repo_id": "new/catalog-model", "installed": False}]})
        return httpx.Response(202, json={"status": "started"})
    client = VoiceStudioClient(settings(tmp_path), transport=httpx.MockTransport(handler))
    await VoiceStudioModelManager(client).install_model("new/catalog-model")
    await client.aclose()
    assert seen == [("GET", "/models"), ("POST", "/models/install")]


@pytest.mark.asyncio
async def test_archetype_uses_offset_pagination(tmp_path) -> None:
    offsets = []
    def handler(request: httpx.Request):
        offsets.append(int(request.url.params["offset"]))
        offset = offsets[-1]
        items = [{"id": f"v{offset}", "name": "Mẫu", "language": "vi"}]
        return httpx.Response(200, json={"total": 2, "limit": 1, "offset": offset, "items": items})
    client = VoiceStudioClient(settings(tmp_path), transport=httpx.MockTransport(handler))
    async def capable(_): return True
    library = VoiceStudioVoiceLibrary(client, None, clone_capability=capable, archetype_page_size=1)
    items = await library.list_archetypes(VoiceFilter(language="vi"))
    await client.aclose()
    assert offsets == [0, 1] and len(items) == 2


def test_public_spec_routes_are_registered(tmp_path) -> None:
    class Empty:
        async def close(self): pass
    app = create_app(settings(tmp_path), {"jobs": Empty()})
    paths = app.openapi()["paths"]
    assert "/v1/speech/engines" in paths
    assert "/v1/speech/model-jobs" in paths
    assert "/v1/speech/jobs/{job_id}" in paths
    assert "/v1/speech/voices/from-archetype" in paths
    assert "/v1/speech/voices/archetypes" in paths
    assert "/v1/speech/voices/presets" in paths
    assert "/v1/speech/runtime-profile" in paths
