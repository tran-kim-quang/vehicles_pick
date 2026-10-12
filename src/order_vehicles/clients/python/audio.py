import asyncio
from collections.abc import AsyncIterator, Callable


class AudioInputOverflow(RuntimeError):
    pass


class SoxrPCM16Resampler:
    def __init__(self, source_rate: int, target_rate: int) -> None:
        try:
            import numpy as np
            import soxr
        except ImportError as exc:
            raise RuntimeError(
                "Audio resampling requires: uv sync --extra audio"
            ) from exc
        self.np = np
        self.stream = soxr.ResampleStream(
            source_rate, target_rate, 1, dtype="int16"
        )

    def process(self, data: bytes, *, final: bool = False) -> bytes:
        samples = self.np.frombuffer(data, dtype="<i2")
        result = self.stream.resample_chunk(samples, last=final)
        return result.astype("<i2", copy=False).tobytes()


class SoundDeviceInput:
    def __init__(
        self,
        *,
        queue_frames: int = 100,
        device_module=None,
        resampler_factory: Callable[[int, int], object] = SoxrPCM16Resampler,
    ) -> None:
        self.queue_frames = queue_frames
        self.device_module = device_module
        self.resampler_factory = resampler_factory
        self.queue: asyncio.Queue[bytes | Exception | None] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stream = None
        self.resampler = None
        self._overflow = False

    @staticmethod
    def _import_sounddevice():
        import sounddevice

        return sounddevice

    async def open(self, device: int | None) -> None:
        try:
            module = self.device_module or self._import_sounddevice()
        except ImportError as exc:
            raise RuntimeError(
                "Microphone support requires: uv sync --extra audio"
            ) from exc
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=self.queue_frames)
        rate = 16000
        try:
            module.check_input_settings(
                device=device, channels=1, dtype="int16", samplerate=rate
            )
        except Exception:
            rate = int(module.query_devices(device, "input")["default_samplerate"])
        if rate != 16000:
            self.resampler = self.resampler_factory(rate, 16000)
        self.stream = module.RawInputStream(
            device=device,
            channels=1,
            dtype="int16",
            samplerate=rate,
            blocksize=max(1, rate // 50),
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        if self.loop is None:
            return
        if status and getattr(status, "input_overflow", False):
            self.loop.call_soon_threadsafe(self._mark_overflow)
            return
        data = bytes(indata)
        if self.resampler is not None:
            data = self.resampler.process(data, final=False)
        self.loop.call_soon_threadsafe(self._offer, data)

    def _mark_overflow(self) -> None:
        if self.queue is not None and not self.queue.full():
            self.queue.put_nowait(
                AudioInputOverflow("Audio device reported an input overflow")
            )
        else:
            self._overflow = True

    def _offer(self, data: bytes) -> None:
        if self.queue is None:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            self._overflow = True

    async def frames(self) -> AsyncIterator[bytes]:
        if self.queue is None:
            raise RuntimeError("Input device is not open")
        while True:
            item = await self.queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item
            if self._overflow:
                self._overflow = False
                raise AudioInputOverflow("Microphone input buffer overflowed")

    async def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.resampler is not None:
            tail = self.resampler.process(b"", final=True)
            if tail:
                self._offer(tail)
            self.resampler = None
        if self.queue is not None:
            try:
                self.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
