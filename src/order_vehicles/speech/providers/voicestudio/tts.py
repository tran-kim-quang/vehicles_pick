import asyncio
import json
from collections.abc import AsyncIterator

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import AudioFormat, AudioPacket, SpeechConfig

from .client import VoiceStudioClient


class VoiceStudioTTSAdapter:
    def __init__(self, client: VoiceStudioClient, *, max_audio_bytes: int = 64 * 1024 * 1024) -> None:
        self.client = client
        self.max_audio_bytes = max_audio_bytes

    async def synthesize(self, text: str, config: SpeechConfig) -> AudioPacket:
        packets: list[AudioPacket] = []
        size = 0
        async for packet in self.stream_synthesize(text, config):
            size += len(packet.data)
            if size > self.max_audio_bytes:
                raise SpeechError("AUDIO_TOO_LARGE", "Synthesized audio exceeded size limit")
            packets.append(packet)
        if not packets:
            raise SpeechError("EMPTY_AUDIO", "VoiceStudio returned no audio", True)
        return AudioPacket(
            format=packets[0].format,
            data=b"".join(packet.data for packet in packets),
        )

    async def stream_synthesize(
        self, text: str, config: SpeechConfig
    ) -> AsyncIterator[AudioPacket]:
        url = self.client.websocket_url("/ws/tts")
        request = {
            "text": text,
            "voice": (
                config.voice_ref.provider_voice_id if config.voice_ref else "default"
            ),
            "engine": config.tts.engine_id,
            "language": config.language,
            "speed": config.speed,
        }
        audio_format: AudioFormat | None = None
        odd = b""
        try:
            async with self.client.websocket_connector(
                url, additional_headers=self.client.headers
            ) as socket:
                await socket.send(json.dumps(request, ensure_ascii=False))
                while True:
                    raw = await asyncio.wait_for(
                        socket.recv(),
                        timeout=self.client.settings.inference_inactivity_timeout_seconds,
                    )
                    if isinstance(raw, bytes):
                        if audio_format is None:
                            raise SpeechError(
                                "TTS_PROTOCOL", "Audio arrived before TTS format metadata"
                            )
                        combined = odd + raw
                        even_size = len(combined) - len(combined) % 2
                        odd = combined[even_size:]
                        if even_size:
                            yield AudioPacket(
                                format=audio_format, data=combined[:even_size]
                            )
                        continue

                    payload = json.loads(raw)
                    event_type = payload.get("type")
                    if event_type == "routing":
                        actual = payload.get("engine")
                        if config.exact_model and actual and actual not in {
                            config.tts.engine_id,
                            config.tts.model_id,
                        }:
                            raise SpeechError(
                                "MODEL_MISMATCH",
                                f"Requested TTS {config.tts.engine_id!r}, provider used {actual!r}",
                            )
                    elif event_type == "start":
                        if payload.get("format") not in {"pcm16", "pcm_s16le"}:
                            raise SpeechError("TTS_PROTOCOL", "Unsupported TTS audio format")
                        actual_engine = payload.get("engine")
                        if config.exact_model and (
                            not actual_engine or actual_engine != config.tts.engine_id
                            or config.tts.model_id is not None
                        ):
                            raise SpeechError(
                                "MODEL_MISMATCH",
                                f"Requested TTS {config.tts.engine_id!r}, provider used {actual_engine!r}",
                            )
                        audio_format = AudioFormat(
                            sample_rate=int(payload["sample_rate"]),
                            channels=int(payload.get("channels", 1)),
                        )
                    elif event_type == "error":
                        raise SpeechError(
                            "TTS_UPSTREAM_ERROR",
                            str(payload.get("message") or "VoiceStudio TTS failed"),
                            bool(payload.get("retryable", True)),
                        )
                    elif event_type == "done":
                        if odd:
                            raise SpeechError(
                                "TRUNCATED_PCM", "TTS stream ended with an incomplete PCM sample"
                            )
                        if audio_format is None:
                            raise SpeechError("TTS_PROTOCOL", "TTS completed without format metadata")
                        break
        except SpeechError:
            raise
        except asyncio.TimeoutError as exc:
            raise SpeechError("UPSTREAM_TIMEOUT", "TTS stream timed out", True) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SpeechError("TTS_DISCONNECTED", "TTS stream disconnected", True) from exc
