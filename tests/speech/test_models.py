import json

import httpx
import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import ModelSelection
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.models import VoiceStudioModelManager


def make_manager(tmp_path, handler, *, known=frozenset({"known/model"})):
    client = VoiceStudioClient(
        Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"),
        transport=httpx.MockTransport(handler),
    )
    return VoiceStudioModelManager(client, known_models=known), client


@pytest.mark.asyncio
async def test_catalog_listing_has_no_post_requests(tmp_path) -> None:
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"models": [{"repo_id": "known/model", "installed": False}]})

    manager, client = make_manager(tmp_path, handler)
    models = await manager.list_models()
    await client.aclose()

    assert models[0].installation_state == "available"
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_unknown_model_is_rejected(tmp_path) -> None:
    manager, client = make_manager(tmp_path, lambda _: httpx.Response(500))

    with pytest.raises(SpeechError) as caught:
        await manager.install_model("unknown/model")
    await client.aclose()

    assert caught.value.code == "UNKNOWN_MODEL"


@pytest.mark.asyncio
async def test_install_target_is_local(tmp_path) -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json={"status": "started"})

    manager, client = make_manager(tmp_path, handler)
    await manager.install_model("known/model")
    await client.aclose()

    assert bodies == [{"repo_id": "known/model", "target": "local"}]


@pytest.mark.asyncio
async def test_model_id_only_sent_to_supported_engine(tmp_path) -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"selected": bodies[-1]})

    manager, client = make_manager(tmp_path, handler)
    await manager.select_model(ModelSelection(engine_id="omnivoice", model_id="repo", purpose="tts"))
    await manager.select_model(ModelSelection(engine_id="mlx-audio", model_id="repo", purpose="tts"))
    await client.aclose()

    assert "model_id" not in bodies[0]
    assert bodies[1]["model_id"] == "repo"


@pytest.mark.asyncio
async def test_catalog_maps_incomplete_and_loaded(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [
            {"repo_id": "a", "incomplete": True, "loaded": False},
            {"repo_id": "b", "installed": True, "complete": True, "loaded": True},
        ]})

    manager, client = make_manager(tmp_path, handler, known=frozenset({"a", "b"}))
    models = await manager.list_models()
    await client.aclose()

    assert [model.installation_state for model in models] == ["incomplete", "installed"]
    assert models[1].loaded is True
