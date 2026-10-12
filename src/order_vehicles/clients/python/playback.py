import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from order_vehicles.speech.domain.models import AudioFormat, AudioPacket

from .audio import SoxrPCM16Resampler


@dataclass
class _Chunk:
    response_id: str
    data: bytes | None
    offset: int = 0


class SoundDeviceOutput:
    def __init__(
        self,
        *,
        device_module=None,
        resampler_factory: Callable[[int, int], object] = SoxrPCM16Resampler,
        max_seconds: float = 2.0,
    ) -> None:
        self.device_module = device_module
        self.resampler_factory = resampler_factory
        self.max_seconds = max_seconds
        self.stream = None
        self.source_format: AudioFormat | None = None
        self.device_rate = 0
        self.resampler = None
        self.resampler_factory_args: tuple[int, int] | None = None
        self.queue: deque[_Chunk] = deque()
        self.queued_bytes = 0
        self.max_bytes = 0
        self.finished_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending_ack: str | None = None

    @staticmethod
    def _import_sounddevice():
        import sounddevice

        return sounddevice

    async def open(self, format: AudioFormat, device: int | None) -> None:
        try:
            module = self.device_module or self._import_sounddevice()
        except ImportError as exc:
            raise RuntimeError(
                "Speaker support requires: uv sync --extra audio"
            ) from exc
        self.loop = asyncio.get_running_loop()
        self.source_format = format
        self.device_rate = format.sample_rate
        try:
            module.check_output_settings(
                device=device,
                channels=format.channels,
                dtype="int16",
                samplerate=self.device_rate,
            )
        except Exception:
            self.device_rate = int(
                module.query_devices(device, "output")["default_samplerate"]
            )
        if self.device_rate != format.sample_rate:
            self.resampler_factory_args = (format.sample_rate, self.device_rate)
            self.resampler = self.resampler_factory(*self.resampler_factory_args)
        self.max_bytes = int(
            self.device_rate * format.channels * 2 * self.max_seconds
        )
        self.stream = module.RawOutputStream(
            device=device,
            channels=format.channels,
            dtype="int16",
            samplerate=self.device_rate,
            callback=self._callback,
        )
        self.stream.start()

    def enqueue(self, response_id: str, packet: AudioPacket) -> None:
        if self.source_format is None:
            raise RuntimeError("Output device is not open")
        if packet.format != self.source_format:
            raise ValueError("Audio packet format changed within a response")
        data = packet.data
        if self.resampler is not None:
            data = self.resampler.process(data, final=False)
        if self.queued_bytes + len(data) > self.max_bytes:
            raise BufferError("Speaker output buffer exceeds two seconds")
        self.queue.append(_Chunk(response_id, data))
        self.queued_bytes += len(data)

    def end_response(self, response_id: str) -> None:
        if self.resampler is not None:
            tail = self.resampler.process(b"", final=True)
            if tail:
                if self.queued_bytes + len(tail) > self.max_bytes:
                    raise BufferError("Speaker output buffer exceeds two seconds")
                self.queue.append(_Chunk(response_id, tail))
                self.queued_bytes += len(tail)
            self.resampler = self.resampler_factory(*self.resampler_factory_args)
        self.queue.append(_Chunk(response_id, None))

    def clear(self) -> None:
        self.queue.clear()
        self.queued_bytes = 0
        self.pending_ack = None

    def _callback(self, outdata, frames, time_info, status) -> None:
        outdata[:] = self.render(frames)

    def render(self, frames: int) -> bytes:
        if self.source_format is None:
            raise RuntimeError("Output device is not open")
        self._publish_pending_ack()
        size = frames * self.source_format.channels * 2
        output = bytearray(size)
        cursor = 0
        while cursor < size and self.queue:
            chunk = self.queue[0]
            if chunk.data is None:
                self.queue.popleft()
                self.pending_ack = chunk.response_id
                continue
            count = min(size - cursor, len(chunk.data) - chunk.offset)
            output[cursor : cursor + count] = chunk.data[
                chunk.offset : chunk.offset + count
            ]
            cursor += count
            chunk.offset += count
            self.queued_bytes -= count
            if chunk.offset == len(chunk.data):
                self.queue.popleft()
        while self.queue and self.queue[0].data is None:
            self.pending_ack = self.queue.popleft().response_id
        return bytes(output)

    def _publish_pending_ack(self) -> None:
        if self.pending_ack is None:
            return
        response_id = self.pending_ack
        self.pending_ack = None
        if self.loop is not None:
            self.loop.call_soon_threadsafe(
                self.finished_queue.put_nowait, response_id
            )

    async def finished(self) -> AsyncIterator[str]:
        while True:
            item = await self.finished_queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.clear()
        self.finished_queue.put_nowait(None)
