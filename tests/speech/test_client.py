import httpx
import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient


@pytest.mark.asyncio
async def test_management_root_is_not_v1(tmp_path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    client = VoiceStudioClient(
        Settings(state_dir=tmp_path / "state", output_dir=tmp_path / "out"),
        transport=httpx.MockTransport(handler),
    )
    await client.request("GET", "/models")
    await client.aclose()

    assert seen == ["http://127.0.0.1:3900/models"]


@pytest.mark.asyncio
async def test_local_only_403_preserves_reason(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "model setup is local-only"})

    client = VoiceStudioClient(
        Settings(state_dir=tmp_path / "state", output_dir=tmp_path / "out"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SpeechError) as caught:
        await client.request("POST", "/models/install", json={})
    await client.aclose()

    assert caught.value.code == "UPSTREAM_FORBIDDEN"
    assert "local-only" in caught.value.message
