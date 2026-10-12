import os
import time
import wave
from pathlib import Path

import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.models import (
    AudioFormat,
    AudioPacket,
    CloneRequest,
    ModelSelection,
    SpeechConfig,
)
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.models import VoiceStudioModelManager
from order_vehicles.speech.providers.voicestudio.stt import VoiceStudioSTTAdapter
from order_vehicles.speech.providers.voicestudio.tts import VoiceStudioTTSAdapter
from order_vehicles.speech.providers.voicestudio.voices import VoiceStudioVoiceLibrary


@pytest.mark.asyncio
async def test_live_voicestudio_catalog_clone_and_tts() -> None:
    if os.getenv("RUN_SPEECH_SMOKE") != "1":
        pytest.skip("pending: set RUN_SPEECH_SMOKE=1 after preparing VoiceStudio")
    if os.getenv("VOICESTUDIO_REVISION") != "7703ca66da77b8d130e6d1d3b2e67d979fa5a77f":
        pytest.fail("VOICESTUDIO_REVISION must match the reviewed baseline")
    reference_path = Path(os.environ.get("SPEECH_REFERENCE_AUDIO", ""))
    if not reference_path.is_file():
        pytest.fail("SPEECH_REFERENCE_AUDIO must point to a prepared reference clip")

    settings = Settings()
    client = VoiceStudioClient(settings)
    models = VoiceStudioModelManager(client)
    tts = VoiceStudioTTSAdapter(client)

    async def clone_capability(_: str):
        engines = await models.list_engines()
        omni = next((engine for engine in engines if engine.id == "omnivoice"), None)
        return omni.capabilities.get("voice_clone") if omni else None

    voices = VoiceStudioVoiceLibrary(client, tts, clone_capability=clone_capability)
    clone = None
    try:
        catalog = await models.list_models()
        assert catalog, "VoiceStudio returned an empty model catalog"
        tts_selection = ModelSelection(
            engine_id=os.getenv("SPEECH_TTS_ENGINE", "omnivoice"),
            model_id=os.getenv("SPEECH_TTS_MODEL") or None,
            purpose="tts",
        )
        actual_tts = await models.select_model(tts_selection)
        assert actual_tts["engine_id"] == tts_selection.engine_id
        stt_selection = ModelSelection(
            engine_id="capture",
            model_id=os.environ.get("SPEECH_STT_MODEL"),
            purpose="stt_stream",
        )
        actual_stt = await models.select_model(stt_selection)
        assert actual_stt["ready"] is True
        started = time.perf_counter()
        clone = await voices.create_clone(
            reference_path.read_bytes(),
            CloneRequest(name="order-vehicles-smoke", filename=reference_path.name),
        )
        config = SpeechConfig(
            stt=stt_selection,
            tts=tts_selection,
            voice_ref=clone.reference,
        )
        first_audio_at = None
        packets = []
        async for packet in voices.preview_voice(clone.reference, "Xin chào, đây là kiểm tra giọng nói.", config):
            first_audio_at = first_audio_at or time.perf_counter()
            packets.append(packet)
        assert packets and packets[0].format.sample_rate > 0
        assert sum(len(packet.data) for packet in packets) > 0
        assert first_audio_at is not None and first_audio_at >= started

        archetype_id = os.environ.get("SPEECH_ARCHETYPE_ID")
        if not archetype_id:
            pytest.fail("SPEECH_ARCHETYPE_ID must identify a prepared archetype")
        designed = await voices.use_archetype(archetype_id)
        assert designed.reference.kind == "design"

        with wave.open(str(reference_path), "rb") as source:
            if source.getsampwidth() != 2 or source.getnchannels() != 1:
                pytest.fail("SPEECH_REFERENCE_AUDIO must be mono PCM16 WAV for STT smoke")
            pcm = source.readframes(source.getnframes())
            audio_format = AudioFormat(sample_rate=source.getframerate())
        transcript = await VoiceStudioSTTAdapter(client).transcribe(
            AudioPacket(format=audio_format, data=pcm), config
        )
        assert transcript.text.strip()
        print({
            "tts_engine": actual_tts,
            "stt_engine": actual_stt,
            "sample_rate": packets[0].format.sample_rate,
            "first_audio_seconds": first_audio_at - started,
            "hardware": os.getenv("SPEECH_HARDWARE", "unspecified"),
        })
    finally:
        if clone is not None:
            await voices.delete_voice(clone.reference)
        await client.aclose()
