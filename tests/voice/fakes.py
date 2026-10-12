import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from order_vehicles.speech.domain.models import AudioFormat, AudioPacket, TranscriptionEvent


class SocketClosed(Exception):
    pass


class FakeSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict | bytes | None] = asyncio.Queue()
        self.sent: list[dict | bytes] = []
        self.sent_event = asyncio.Event()
        self.closed = False

    async def receive(self):
        value = await self.incoming.get()
        if value is None:
            raise SocketClosed()
        return value

    async def send_json(self, value: dict) -> None:
        self.sent.append(value)
        self.sent_event.set()

    async def send_bytes(self, value: bytes) -> None:
        self.sent.append(value)
        self.sent_event.set()

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    async def push(self, value: dict | bytes | None) -> None:
        await self.incoming.put(value)

    async def wait_for(self, predicate, timeout: float = 1.0):
        async def poll():
            while True:
                for item in self.sent:
                    if isinstance(item, dict) and predicate(item):
                        return item
                self.sent_event.clear()
                await self.sent_event.wait()

        return await asyncio.wait_for(poll(), timeout)


class FakeRuntime:
    def __init__(self, summaries: list[str] | None = None) -> None:
        self.summaries = asyncio.Queue()
        for summary in summaries or []:
            self.summaries.put_nowait(summary)
        self.transcribe_calls = 0
        self.closed = False
        self.spoken: list[str] = []
        self.speak_gate: asyncio.Event | None = None

    async def transcribe(self, frames: AsyncIterator[bytes]):
        self.transcribe_calls += 1
        async for _ in frames:
            pass
        text = await self.summaries.get()
        yield TranscriptionEvent(kind="partial", text=text[:1])
        yield TranscriptionEvent(kind="final", final_kind="utterance", text=text)
        yield TranscriptionEvent(kind="final", final_kind="summary", text=text)

    async def speak(self, text: str):
        self.spoken.append(text)
        if self.speak_gate:
            await self.speak_gate.wait()
        yield AudioPacket(
            format=AudioFormat(sample_rate=24000), data=text.encode()
        )


class FakeRuntimeService:
    def __init__(self, runtime: FakeRuntime) -> None:
        self.runtime = runtime
        self.active = 0

    @asynccontextmanager
    async def open(self, profile=None):
        self.active += 1
        try:
            yield self.runtime
        finally:
            self.active -= 1
            self.runtime.closed = True


class FakeExtractor:
    def __init__(self, results) -> None:
        self.results = asyncio.Queue()
        for result in results:
            self.results.put_nowait(result)
        self.calls = 0
        self.gates: list[asyncio.Event] = []

    async def extract(self, transcript, session):
        index = self.calls
        self.calls += 1
        if index < len(self.gates):
            await self.gates[index].wait()
        return await self.results.get()
