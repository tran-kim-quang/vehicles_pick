import wave
from io import BytesIO

import pytest

from order_vehicles.clients.python.recording import record_reference


class FakeInput:
    def __init__(self):
        self.opened = False
        self.closed = False

    async def open(self, device):
        self.opened = True

    async def frames(self):
        while True:
            yield b"\x01\x00" * 320

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_recorded_clone_is_separate_from_booking_session() -> None:
    source = FakeInput()
    wav = await record_reference(0.04, None, input_source=source)
    with wave.open(BytesIO(wav), "rb") as audio:
        assert audio.getframerate() == 16000
        assert audio.getnchannels() == 1
        assert audio.getnframes() == 640
    assert source.opened and source.closed
