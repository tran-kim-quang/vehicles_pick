import io
import json
import wave

import httpx
import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import RuntimeProfile, VoiceRef
from order_vehicles.speech.providers.vieneu import VieNeuCloudTTS


def profile():
    return RuntimeProfile.default().model_copy(update={
        "voice_ref": VoiceRef(provider_id="vieneu", kind="preset", provider_voice_id="Ngọc Lan")
    }).speech_config()


@pytest.mark.asyncio
async def test_cloud_request_and_wav_sample_rate():
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x01\x00\x02\x00")

    async def handler(request):
        assert request.url == "https://api.vieneu.io/api/v1/audio/speech"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["voice"] == "Ngọc Lan"
        assert body["model"] == "vieneu-v4" and body["response_format"] == "wav"
        return httpx.Response(200, content=audio.getvalue())

    settings = Settings(OV_VIENEU_API_KEY="test-key", OV_VIENEU_BASE_URL="https://api.vieneu.io/api/v1")
    adapter = VieNeuCloudTTS(settings, transport=httpx.MockTransport(handler))
    try:
        packet = await adapter.synthesize("Xin chào", profile())
        assert packet.format.sample_rate == 48000 and packet.data == b"\x01\x00\x02\x00"
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_cloud_auth_error_does_not_expose_key():
    async def handler(request):
        return httpx.Response(401, json={"error": {"message": "secret-key"}})
    adapter = VieNeuCloudTTS(Settings(OV_VIENEU_API_KEY="secret-key"), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SpeechError) as caught:
            await adapter.synthesize("Xin chào", profile())
        assert caught.value.code == "VIENEU_UNAUTHORIZED"
        assert "secret-key" not in str(caught.value)
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_cloud_audio_is_chunked_for_bounded_speaker_buffer():
    pcm = b"\x01\x00" * (48000 * 3)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(pcm)
    async def handler(request):
        return httpx.Response(200, content=audio.getvalue())
    adapter = VieNeuCloudTTS(Settings(OV_VIENEU_API_KEY="test"), transport=httpx.MockTransport(handler))
    try:
        packets = [p async for p in adapter.stream_synthesize("Xin chào", profile())]
        assert len(packets) > 1
        assert all(len(p.data) <= 9600 for p in packets)
        assert b"".join(p.data for p in packets) == pcm
    finally:
        await adapter.aclose()
