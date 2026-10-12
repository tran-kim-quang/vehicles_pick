import asyncio

import pytest

from order_vehicles.clients.python.playback import SoundDeviceOutput
from order_vehicles.speech.domain.models import AudioFormat, AudioPacket

from .test_audio import FakeSoundDevice, FakeStream


class FakeOutputDevice(FakeSoundDevice):
    def check_output_settings(self, **kwargs):
        self.requested_rates.append(kwargs["samplerate"])
        if kwargs["samplerate"] == 24000:
            raise ValueError("unsupported")

    def RawOutputStream(self, **kwargs):
        self.stream = FakeStream(**kwargs)
        return self.stream


@pytest.mark.asyncio
async def test_output_48000_is_not_played_as_24000() -> None:
    device = FakeOutputDevice()
    output = SoundDeviceOutput(device_module=device)
    await output.open(AudioFormat(sample_rate=48000), None)
    assert device.stream.kwargs["samplerate"] == 48000
    await output.close()


@pytest.mark.asyncio
async def test_unsupported_output_rate_resamples_to_device_rate() -> None:
    class Resampler:
        def __init__(self):
            self.calls = []

        def process(self, data, *, final=False):
            self.calls.append((data, final))
            return b"\x02\x00" * 2 if data else b""

    device = FakeOutputDevice()
    created = []

    def factory(source, target):
        created.append((source, target, Resampler()))
        return created[-1][2]

    output = SoundDeviceOutput(device_module=device, resampler_factory=factory)
    source_format = AudioFormat(sample_rate=24000)
    await output.open(source_format, None)
    output.enqueue("r1", AudioPacket(format=source_format, data=b"\x01\x00"))
    assert device.stream.kwargs["samplerate"] == 48000
    assert output.render(2) == b"\x02\x00" * 2
    assert created[0][:2] == (24000, 48000)
    await output.close()


@pytest.mark.asyncio
async def test_clear_prevents_buffered_old_response_playback() -> None:
    output = SoundDeviceOutput(device_module=FakeOutputDevice())
    await output.open(AudioFormat(sample_rate=48000), None)
    output.enqueue("old", AudioPacket(format=AudioFormat(sample_rate=48000), data=b"old"))
    output.clear()
    assert output.render(3) == b"\x00" * 6
    await output.close()


@pytest.mark.asyncio
async def test_ack_after_device_playback_not_network_done() -> None:
    output = SoundDeviceOutput(device_module=FakeOutputDevice())
    await output.open(AudioFormat(sample_rate=48000), None)
    output.enqueue("r1", AudioPacket(format=AudioFormat(sample_rate=48000), data=b"\x01\x00" * 2))
    output.end_response("r1")
    finished = output.finished()
    assert output.render(2) == b"\x01\x00" * 2
    pending = asyncio.create_task(anext(finished))
    await asyncio.sleep(0)
    assert not pending.done()
    output.render(1)
    assert await pending == "r1"
    await output.close()
