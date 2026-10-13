import json
import io
import wave
import httpx

import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import ModelSelection, SpeechConfig, VoiceRef
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.tts import VoiceStudioTTSAdapter
from tests.speech.fakes import ScriptedConnector


def config() -> SpeechConfig:
    return SpeechConfig(
        stt=ModelSelection(engine_id="capture", purpose="stt_stream"),
        tts=ModelSelection(engine_id="omnivoice", model_id="omni", purpose="tts"),
        voice_ref=VoiceRef(
            provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234"
        ),
    )


def client_for(tmp_path, connector: ScriptedConnector) -> VoiceStudioClient:
    return VoiceStudioClient(
        Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"),
        websocket_connector=connector,
    )


@pytest.mark.asyncio
async def test_design_voice_uses_ui_generation_with_saved_profile(tmp_path):
    wav = io.BytesIO()
    with wave.open(wav, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(b"\x01\x00\x02\x00")

    async def handler(request):
        assert request.url.path == "/generate"
        body = request.content.decode()
        assert "profile_id=36fc8123" in body and "language=vi" in body
        assert "seed=" not in body  # The backend restores the saved profile seed.
        return httpx.Response(200, content=wav.getvalue(), headers={"content-type": "audio/wav"})

    client = VoiceStudioClient(Settings(), transport=httpx.MockTransport(handler), websocket_connector=ScriptedConnector([[]]))
    settings = config().model_copy(update={"voice_ref": VoiceRef(provider_id="voicestudio", kind="design", provider_voice_id="36fc8123")})
    try:
        packets = [p async for p in VoiceStudioTTSAdapter(client).stream_synthesize("Xin chào", settings)]
        assert packets[0].format.sample_rate == 24000
        assert packets[0].data == b"\x01\x00\x02\x00"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_start_metadata_48000_is_preserved(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "start", "sample_rate": 48000, "channels": 1, "format": "pcm16"},
        b"\x01\x00\x02\x00",
        {"type": "done"},
    ]])

    packets = [packet async for packet in VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("Xin chào", config())]

    assert packets[0].format.sample_rate == 48000
    assert b"".join(packet.data for packet in packets) == b"\x01\x00\x02\x00"
    sent = json.loads(connector.connections[0].sent[0])
    assert sent["voice"] == "abcd1234"


@pytest.mark.asyncio
async def test_audio_before_start_is_error(tmp_path) -> None:
    connector = ScriptedConnector([[b"\x00\x00"]])

    with pytest.raises(SpeechError) as caught:
        _ = [packet async for packet in VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("x", config())]

    assert caught.value.code == "TTS_PROTOCOL"


@pytest.mark.asyncio
async def test_routing_event_is_handled(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "routing", "engine": "omnivoice"},
        {"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16"},
        b"\x00\x00",
        {"type": "done"},
    ]])

    packets = [packet async for packet in VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("x", config())]

    assert packets[0].data == b"\x00\x00"


@pytest.mark.asyncio
async def test_error_before_audio(tmp_path) -> None:
    connector = ScriptedConnector([[{"type": "error", "message": "GPU busy"}]])

    with pytest.raises(SpeechError) as caught:
        _ = [packet async for packet in VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("x", config())]

    assert caught.value.code == "TTS_UPSTREAM_ERROR"
    assert "GPU busy" in caught.value.message


@pytest.mark.asyncio
async def test_cancel_closes_upstream(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16"},
        b"\x00\x00",
        b"\x01\x00",
    ]])
    stream = VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("x", config())

    await anext(stream)
    await stream.aclose()

    assert connector.open_connections == 0


@pytest.mark.asyncio
async def test_new_response_never_receives_old_bytes(tmp_path) -> None:
    connector = ScriptedConnector([
        [{"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16"}, b"\x01\x00", {"type": "done"}],
        [{"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16"}, b"\x02\x00", {"type": "done"}],
    ])
    adapter = VoiceStudioTTSAdapter(client_for(tmp_path, connector))

    first = b"".join([p.data async for p in adapter.stream_synthesize("one", config())])
    second = b"".join([p.data async for p in adapter.stream_synthesize("two", config())])

    assert first == b"\x01\x00"
    assert second == b"\x02\x00"
    assert len(connector.connections) == 2


@pytest.mark.asyncio
async def test_odd_pcm_byte_is_buffered_until_next_frame(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16"},
        b"\x01",
        b"\x00\x02\x00",
        {"type": "done"},
    ]])

    packets = [packet async for packet in VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("x", config())]

    assert b"".join(packet.data for packet in packets) == b"\x01\x00\x02\x00"


@pytest.mark.asyncio
async def test_terminal_odd_pcm_byte_is_rejected(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "start", "sample_rate": 24000, "channels": 1, "format": "pcm16"},
        b"\x01",
        {"type": "done"},
    ]])

    with pytest.raises(SpeechError) as caught:
        _ = [packet async for packet in VoiceStudioTTSAdapter(client_for(tmp_path, connector)).stream_synthesize("x", config())]

    assert caught.value.code == "TRUNCATED_PCM"
