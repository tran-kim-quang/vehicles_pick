import wave
from pathlib import Path

from order_vehicles.speech.domain.models import AudioFormat, AudioPacket


GREETING_TEXT = "xin chào, mình có thể giúp gì cho bạn ạ"


def load_greeting(path: Path) -> AudioPacket | None:
    if not path.is_file():
        return None
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("Greeting audio must be PCM16 WAV")
        return AudioPacket(
            format=AudioFormat(sample_rate=wav.getframerate(), channels=wav.getnchannels()),
            data=wav.readframes(wav.getnframes()),
        )
