import os
from pathlib import Path

import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.models import CloneRequest, ModelSelection, SpeechConfig
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.models import VoiceStudioModelManager
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
    try:
        catalog = await models.list_models()
        assert catalog, "VoiceStudio returned an empty model catalog"
        clone = await voices.create_clone(
            reference_path.read_bytes(),
            CloneRequest(name="order-vehicles-smoke", filename=reference_path.name),
        )
        config = SpeechConfig(
            stt=ModelSelection(engine_id="capture", purpose="stt_stream"),
            tts=ModelSelection(engine_id="omnivoice", purpose="tts"),
            voice_ref=clone.reference,
        )
        packets = [packet async for packet in voices.preview_voice(clone.reference, "Xin chào, đây là kiểm tra giọng nói.", config)]
        assert packets and packets[0].format.sample_rate > 0
        assert sum(len(packet.data) for packet in packets) > 0
    finally:
        await client.aclose()
