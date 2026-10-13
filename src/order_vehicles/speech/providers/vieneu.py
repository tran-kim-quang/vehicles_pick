from io import BytesIO
import wave

import httpx

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import AudioFormat, AudioPacket, SpeechConfig


class VieNeuCloudTTS:
    def __init__(self, settings: Settings, *, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.vieneu_base_url + "/",
            headers={"Authorization": "Bearer " + settings.vieneu_api_key.get_secret_value()},
            timeout=settings.vieneu_timeout_seconds,
            transport=transport,
        )

    async def aclose(self):
        await self.client.aclose()

    async def synthesize(self, text: str, config: SpeechConfig) -> AudioPacket:
        if not self.settings.vieneu_api_key.get_secret_value():
            raise SpeechError("VIENEU_NOT_CONFIGURED", "Configure OV_VIENEU_API_KEY or TTS_API_KEY")
        voice = self.settings.vieneu_voice
        if config.voice_ref:
            if config.voice_ref.provider_id != "vieneu":
                raise SpeechError("VOICE_PROVIDER_MISMATCH", "Select a VieNeu voice for cloud TTS")
            voice = config.voice_ref.provider_voice_id
        try:
            response = await self.client.post("audio/speech", json={
                "model": self.settings.vieneu_model, "voice": voice,
                "input": text, "response_format": "wav", "speed": config.speed,
            })
        except httpx.TimeoutException as exc:
            raise SpeechError("VIENEU_TIMEOUT", "VieNeu synthesis timed out", True) from exc
        except httpx.HTTPError as exc:
            raise SpeechError("VIENEU_UNAVAILABLE", "VieNeu Cloud is unavailable", True) from exc
        if response.is_error:
            code = {401: "VIENEU_UNAUTHORIZED", 403: "VIENEU_FORBIDDEN", 429: "VIENEU_RATE_LIMIT"}.get(response.status_code, "VIENEU_ERROR")
            raise SpeechError(code, f"VieNeu Cloud returned HTTP {response.status_code}", response.status_code == 429 or response.status_code >= 500)
        if len(response.content) > 64 * 1024 * 1024:
            raise SpeechError("AUDIO_TOO_LARGE", "VieNeu audio exceeded size limit")
        try:
            with wave.open(BytesIO(response.content), "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise SpeechError("TTS_PROTOCOL", "Expected PCM16 WAV from VieNeu")
                fmt = AudioFormat(sample_rate=wav.getframerate(), channels=wav.getnchannels())
                pcm = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError) as exc:
            raise SpeechError("TTS_PROTOCOL", "Invalid WAV from VieNeu") from exc
        if not pcm:
            raise SpeechError("EMPTY_AUDIO", "VieNeu returned no audio", True)
        return AudioPacket(format=fmt, data=pcm)

    async def stream_synthesize(self, text: str, config: SpeechConfig):
        packet = await self.synthesize(text, config)
        chunk_bytes = packet.format.sample_rate // 10 * packet.format.channels * 2
        for offset in range(0, len(packet.data), chunk_bytes):
            yield AudioPacket(format=packet.format, data=packet.data[offset:offset + chunk_bytes])
