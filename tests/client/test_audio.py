import asyncio

import pytest

from order_vehicles.clients.python.audio import AudioInputOverflow, SoundDeviceInput


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.stream = None
        self.requested_rates = []

    def check_input_settings(self, **kwargs):
        self.requested_rates.append(kwargs["samplerate"])
        if kwargs["samplerate"] == 16000:
            raise ValueError("unsupported")

    def query_devices(self, device, kind):
        return {"default_samplerate": 48000}

    def RawInputStream(self, **kwargs):
        self.stream = FakeStream(**kwargs)
        return self.stream


class FakeResampler:
    def __init__(self):
        self.calls = []

    def process(self, data, *, final=False):
        self.calls.append((data, final))
        return b"16k:" + data


@pytest.mark.asyncio
async def test_unsupported_device_rate_resamples_statefully() -> None:
    device = FakeSoundDevice()
    resampler = FakeResampler()
    source = SoundDeviceInput(
        device_module=device,
        resampler_factory=lambda _source, _target: resampler,
    )
    await source.open(None)
    device.stream.kwargs["callback"](b"native", 320, None, None)
    frame = await anext(source.frames())
    await source.close()
    assert device.stream.kwargs["samplerate"] == 48000
    assert frame == b"16k:native"
    assert resampler.calls == [(b"native", False), (b"", True)]


@pytest.mark.asyncio
async def test_input_queue_overflow_is_visible() -> None:
    device = FakeSoundDevice()
    source = SoundDeviceInput(
        queue_frames=1,
        device_module=device,
        resampler_factory=lambda *_: FakeResampler(),
    )
    await source.open(None)
    callback = device.stream.kwargs["callback"]
    callback(b"one", 320, None, None)
    callback(b"two", 320, None, None)
    await asyncio.sleep(0)
    iterator = source.frames()
    await anext(iterator)
    with pytest.raises(AudioInputOverflow):
        await anext(iterator)
    await source.close()


def test_audio_import_failure_explains_extra(monkeypatch) -> None:
    source = SoundDeviceInput()
    monkeypatch.setattr(source, "_import_sounddevice", lambda: (_ for _ in ()).throw(ImportError()))
    with pytest.raises(RuntimeError, match="audio"):
        asyncio.run(source.open(None))
