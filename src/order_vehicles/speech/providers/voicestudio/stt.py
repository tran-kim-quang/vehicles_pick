import asyncio
import json
import wave
from collections.abc import AsyncIterator
from contextlib import suppress
from io import BytesIO
from urllib.parse import urlencode

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import (
    AudioPacket,
    SpeechConfig,
    TranscriptionEvent,
)

from .client import VoiceStudioClient


class VoiceStudioSTTAdapter:
    def __init__(self, client: VoiceStudioClient) -> None:
        self.client = client

    async def transcribe(
        self, audio: AudioPacket, config: SpeechConfig
    ) -> TranscriptionEvent:
        if audio.format.encoding != "pcm_s16le":
            raise SpeechError("UNSUPPORTED_AUDIO", "STT requires pcm_s16le")
        wav = self._as_wav(audio)
        response = await self.client.request(
            "POST",
            "/v1/audio/transcriptions",
            files={"file": ("audio.wav", wav, "audio/wav")},
            data={"model": config.stt.model_id or config.stt.engine_id, "language": config.language},
        )
        payload = response.json()
        actual = payload.get("engine") or payload.get("model")
        self._ensure_expected_engine(actual, config, payload.get("model"))
        return TranscriptionEvent(
            kind="final",
            final_kind="summary",
            text=str(payload.get("text", "")),
            actual_engine=actual,
            actual_model=payload.get("model"),
        )

    async def stream_transcribe(
        self, frames: AsyncIterator[bytes], config: SpeechConfig
    ) -> AsyncIterator[TranscriptionEvent]:
        query: dict[str, str] = {"pcm": "1", "sr": "16000"}
        if config.language and config.language.lower() != "auto":
            query["language"] = config.language
        if config.stt.model_id:
            query["model"] = config.stt.model_id
        if config.stt.engine_id == "faster-whisper":
            query["engine"] = "faster-whisper"
        path = f"/v1/audio/transcriptions/stream?{urlencode(query)}"
        url = self.client.websocket_url(path)
        sender: asyncio.Task[None] | None = None
        try:
            async with self.client.websocket_connector(
                url, additional_headers=self.client.headers
            ) as socket:
                sender = asyncio.create_task(self._send_frames(socket, frames))
                await asyncio.sleep(0)
                while True:
                    raw = await asyncio.wait_for(
                        socket.recv(),
                        timeout=self.client.settings.inference_inactivity_timeout_seconds,
                    )
                    if isinstance(raw, bytes):
                        raise SpeechError("STT_PROTOCOL", "Unexpected binary STT event")
                    payload = json.loads(raw)
                    event = self._event(payload, config)
                    if event is None:
                        continue
                    yield event
                    if event.final_kind == "summary":
                        await sender
                        break
        except SpeechError:
            raise
        except asyncio.TimeoutError as exc:
            raise SpeechError("UPSTREAM_TIMEOUT", "STT stream timed out", True) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SpeechError("STT_DISCONNECTED", "STT stream disconnected before summary", True) from exc
        finally:
            if sender and not sender.done():
                sender.cancel()
                with suppress(asyncio.CancelledError):
                    await sender

    async def _send_frames(self, socket: object, frames: AsyncIterator[bytes]) -> None:
        async for frame in frames:
            await socket.send(frame)  # type: ignore[attr-defined]
        await socket.send(json.dumps({"type": "input_audio.end"}))  # type: ignore[attr-defined]

    def _event(
        self, payload: dict[str, object], config: SpeechConfig
    ) -> TranscriptionEvent | None:
        event_type = str(payload.get("type", ""))
        if event_type in {"transcript.partial", "partial"}:
            return TranscriptionEvent(kind="partial", text=str(payload.get("text", "")))
        if event_type not in {"transcript.final", "final"}:
            return None
        final_kind = str(payload.get("final_kind", "utterance"))
        if final_kind not in {"utterance", "summary"}:
            return None
        actual = payload.get("engine") or payload.get("actual_engine")
        actual_engine = str(actual) if actual else None
        actual_model_raw = payload.get("model") or payload.get("model_id")
        actual_model = str(actual_model_raw) if actual_model_raw else None
        self._ensure_expected_engine(actual_engine, config, actual_model)
        return TranscriptionEvent(
            kind="final",
            final_kind=final_kind,
            text=str(payload.get("text", "")),
            actual_engine=actual_engine,
            actual_model=actual_model,
        )

    @staticmethod
    def _ensure_expected_engine(actual: str | None, config: SpeechConfig, actual_model: str | None = None) -> None:
        if config.exact_model and (
            not actual
            or actual != config.stt.engine_id
            or (config.stt.model_id is not None and actual_model != config.stt.model_id)
        ):
            raise SpeechError(
                "MODEL_MISMATCH",
                f"Requested STT {config.stt.engine_id!r}, provider used {actual!r}",
            )

    @staticmethod
    def _as_wav(audio: AudioPacket) -> bytes:
        stream = BytesIO()
        with wave.open(stream, "wb") as output:
            output.setnchannels(audio.format.channels)
            output.setsampwidth(2)
            output.setframerate(audio.format.sample_rate)
            output.writeframes(audio.data)
        return stream.getvalue()
