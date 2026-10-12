import asyncio
from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

from order_vehicles.clients.python.cli import app
from order_vehicles.clients.python.microphone import PythonMicClient
from order_vehicles.clients.python.results import AtomicBookingWriter
from order_vehicles.clients.python.turns import TurnController

from .test_results import payload


class MarkerVAD:
    def is_speech(self, pcm):
        return pcm == b"speech"


class FakeInput:
    def __init__(self):
        self.closed = False

    async def open(self, device):
        pass

    async def frames(self):
        for _ in range(10):
            yield b"speech"
        for _ in range(35):
            yield b"silence"
        while True:
            await asyncio.sleep(3600)

    async def close(self):
        self.closed = True


class FakeOutput:
    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.closed = False
        self.finished_queue = asyncio.Queue()
        self.opened = False

    async def open(self, format, device):
        self.opened = True

    def enqueue(self, response_id, packet):
        self.log.append(("audio", response_id))

    def end_response(self, response_id):
        self.finished_queue.put_nowait(response_id)

    def clear(self):
        self.log.append(("clear", None))

    async def finished(self):
        while True:
            yield await self.finished_queue.get()

    async def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self, incoming, log=None):
        self.incoming = asyncio.Queue()
        for item in incoming:
            self.incoming.put_nowait(item)
        self.sent = []
        self.closed = False
        self.log = log if log is not None else []

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, value):
        self.sent.append(value)
        self.log.append(("json", value["type"]))

    async def send_bytes(self, value):
        self.sent.append(value)

    async def close(self, code=1000):
        self.closed = True


class FakeTransport:
    def __init__(self, socket):
        self.socket = socket

    @asynccontextmanager
    async def connect(self, url):
        yield self.socket


def incoming_events():
    return [
        {"type": "session.started", "session_id": "s", "request_id": "r"},
        {
            "type": "assistant.audio.start",
            "response_id": "final",
            "format": {"encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1},
        },
        b"audio",
        {"type": "booking.confirmed", "payload": payload()},
        {"type": "assistant.audio.end", "response_id": "final"},
    ]


@pytest.mark.asyncio
async def test_fake_microphone_completes_booking(tmp_path) -> None:
    source = FakeInput()
    output = FakeOutput()
    socket = FakeSocket(incoming_events())
    writer = AtomicBookingWriter(tmp_path)
    messages = []
    client = PythonMicClient(
        input_source=source,
        output=output,
        transport=FakeTransport(socket),
        writer=writer,
        turn_controller=TurnController(MarkerVAD()),
        echo=messages.append,
    )
    await asyncio.wait_for(client.run("ws://test", None, None, False), 1)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert "đã lưu" in " ".join(messages).lower()
    assert source.closed and output.closed and socket.closed


@pytest.mark.asyncio
async def test_client_clears_audio_before_sending_interrupt(tmp_path) -> None:
    log = []
    socket = FakeSocket(incoming_events(), log)
    client = PythonMicClient(
        input_source=FakeInput(),
        output=FakeOutput(log),
        transport=FakeTransport(socket),
        writer=AtomicBookingWriter(tmp_path),
        turn_controller=TurnController(MarkerVAD()),
        echo=lambda _: None,
    )
    await asyncio.wait_for(client.run("ws://test", None, None, False), 1)
    clear_index = log.index(("clear", None))
    interrupt_index = log.index(("json", "response.interrupt"))
    assert clear_index < interrupt_index


@pytest.mark.asyncio
async def test_write_failure_does_not_print_success(tmp_path, monkeypatch) -> None:
    writer = AtomicBookingWriter(tmp_path)
    monkeypatch.setattr(writer, "_atomic_write", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    messages = []
    client = PythonMicClient(
        input_source=FakeInput(), output=FakeOutput(),
        transport=FakeTransport(FakeSocket(incoming_events())), writer=writer,
        turn_controller=TurnController(MarkerVAD()), echo=messages.append,
    )
    await asyncio.wait_for(client.run("ws://test", None, None, False), 1)
    assert "đã lưu" not in " ".join(messages).lower()
    assert writer.pending


@pytest.mark.asyncio
async def test_ctrl_c_cancellation_closes_devices_and_socket(tmp_path) -> None:
    source = FakeInput()
    output = FakeOutput()
    socket = FakeSocket([])
    client = PythonMicClient(
        input_source=source, output=output, transport=FakeTransport(socket),
        writer=AtomicBookingWriter(tmp_path),
        turn_controller=TurnController(MarkerVAD()), echo=lambda _: None,
    )
    task = asyncio.create_task(client.run("ws://test", None, None, False))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.closed and output.closed and socket.closed


def test_microphone_and_record_commands_expose_device_flags() -> None:
    mic_help = CliRunner().invoke(app, ["mic", "--help"])
    record_help = CliRunner().invoke(app, ["voices", "record", "--help"])
    assert mic_help.exit_code == 0
    assert "--input-device" in mic_help.output
    assert "--push-to-talk" in mic_help.output
    assert record_help.exit_code == 0
    assert "--name" in record_help.output and "--seconds" in record_help.output
