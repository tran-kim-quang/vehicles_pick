import json

import httpx
import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import (
    AudioFormat,
    AudioPacket,
    CloneRequest,
    ModelSelection,
    SpeechConfig,
    VoiceFilter,
    VoiceRef,
)
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.presets import verified_presets
from order_vehicles.speech.providers.voicestudio.voices import VoiceStudioVoiceLibrary


class FakeTTS:
    def __init__(self) -> None:
        self.calls = []

    async def stream_synthesize(self, text, config):
        self.calls.append((text, config))
        yield AudioPacket(format=AudioFormat(sample_rate=48000), data=b"\x00\x00")


def speech_config() -> SpeechConfig:
    return SpeechConfig(
        stt=ModelSelection(engine_id="capture", purpose="stt_stream"),
        tts=ModelSelection(engine_id="omnivoice", purpose="tts"),
    )


def make_library(tmp_path, handler, *, capability=True, tts=None):
    client = VoiceStudioClient(
        Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"),
        transport=httpx.MockTransport(handler),
    )

    async def checker(_: str):
        return capability

    return VoiceStudioVoiceLibrary(client, tts or FakeTTS(), clone_capability=checker), client


@pytest.mark.asyncio
async def test_clone_uses_multipart_reference(tmp_path) -> None:
    captured = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request.content
        return httpx.Response(200, json={"id": "abcd1234", "name": "Giọng Nam", "kind": "clone", "language": "vi"})

    library, client = make_library(tmp_path, handler)
    clone = await library.create_clone(
        b"RIFFaudio", CloneRequest(name="Giọng Nam", ref_text="xin chào", filename="nam.wav")
    )
    await client.aclose()

    assert clone.reference.provider_voice_id == "abcd1234"
    assert clone.reference.kind == "clone"
    assert b'name="ref_audio"; filename="nam.wav"' in captured
    assert b'name="ref_text"' in captured and b"xin ch\xc3\xa0o" in captured
    assert b'name="language"' in captured and b"vi" in captured


@pytest.mark.asyncio
async def test_reference_playback_is_not_synthesis_preview(tmp_path) -> None:
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(404)

    tts = FakeTTS()
    library, client = make_library(tmp_path, handler, tts=tts)
    ref = VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234")

    packets = [p async for p in library.preview_voice(ref, "Nội dung mới", speech_config())]
    await client.aclose()

    assert packets[0].format.sample_rate == 48000
    assert tts.calls[0][0] == "Nội dung mới"
    assert requested == []


@pytest.mark.asyncio
async def test_clone_unsupported_fails_before_upload(tmp_path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    library, client = make_library(tmp_path, handler, capability=None)

    with pytest.raises(SpeechError) as caught:
        await library.create_clone(b"RIFF", CloneRequest(name="x", filename="x.wav"))
    await client.aclose()

    assert caught.value.code == "CAPABILITY_UNVERIFIED"
    assert calls == 0


@pytest.mark.asyncio
async def test_archetype_use_resolves_profile_id(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/archetypes/calm/use"
        return httpx.Response(200, json={"profile_id": "design123", "name": "Bình tĩnh"})

    library, client = make_library(tmp_path, handler)
    voice = await library.use_archetype("calm")
    await client.aclose()

    assert voice.reference.kind == "design"
    assert voice.reference.provider_voice_id == "design123"


@pytest.mark.asyncio
async def test_profiles_and_aliases_are_distinct(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles":
            return httpx.Response(200, json={"profiles": [{"id": "abcd1234", "name": "Nam", "kind": "clone"}]})
        return httpx.Response(200, json={"voices": [{"id": "alloy", "name": "Alloy"}]})

    library, client = make_library(tmp_path, handler)
    voices = await library.list_voices(VoiceFilter())
    await client.aclose()

    assert {(v.reference.kind, v.reference.provider_voice_id) for v in voices} == {("clone", "abcd1234"), ("default", "alloy")}
    assert next(v for v in voices if v.reference.kind == "default").default_alias is True


@pytest.mark.asyncio
async def test_pagination_collects_matching_archetypes(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(200, json={"items": [{"id": "one", "name": "Một", "language": "vi"}], "has_more": True})
        return httpx.Response(200, json={"items": [{"id": "two", "name": "Hai", "language": "en"}], "has_more": False})

    library, client = make_library(tmp_path, handler)
    items = await library.list_archetypes(VoiceFilter(language="vi"))
    await client.aclose()

    assert [item["id"] for item in items] == ["one"]


def test_unknown_preset_not_advertised() -> None:
    assert verified_presets("unknown-engine") == []
    assert [voice.provider_voice_id for voice in verified_presets("omnivoice")] == ["default"]


@pytest.mark.asyncio
async def test_unicode_voice_name_does_not_change_id(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abcd1234", "name": "Giọng nữ dịu dàng", "kind": "clone"})

    library, client = make_library(tmp_path, handler)
    voice = await library.create_clone(b"RIFF", CloneRequest(name="Giọng nữ dịu dàng", filename="voice.wav"))
    await client.aclose()

    assert voice.name == "Giọng nữ dịu dàng"
    assert voice.reference.provider_voice_id == "abcd1234"
