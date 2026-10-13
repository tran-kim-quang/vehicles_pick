import pytest

from order_vehicles.speech.application.runtime import SpeechRuntime
from order_vehicles.speech.domain.models import AudioFormat, AudioPacket, RuntimeProfile
from order_vehicles.speech.greeting import GREETING_TEXT


@pytest.mark.asyncio
async def test_greeting_uses_prebuilt_audio_without_tts_call():
    class TTS:
        async def stream_synthesize(self, text, config):
            raise AssertionError("Cached greeting must not call cloud TTS")
            yield None

    audio = AudioPacket(format=AudioFormat(sample_rate=48000), data=b"\x01\x00" * 20)
    runtime = SpeechRuntime(RuntimeProfile.default(), None, TTS(), greeting_audio=audio)
    packets = [packet async for packet in runtime.speak(GREETING_TEXT)]
    assert packets == [audio]
