import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

from order_vehicles.speech.domain.models import AudioFormat, AudioPacket

from .audio import AudioInputOverflow, SoundDeviceInput
from .playback import SoundDeviceOutput
from .results import AtomicBookingWriter, ResultConflict, ResultWriteError
from .transport import PythonSessionTransport
from .turns import TurnController
from .vad import EnergyVAD


class PythonMicClient:
    def __init__(
        self,
        *,
        input_source=None,
        output=None,
        transport=None,
        writer: AtomicBookingWriter,
        turn_controller=None,
        push_to_talk_commands=None,
        echo=print,
    ) -> None:
        self.input_source = input_source or SoundDeviceInput()
        self.output = output or SoundDeviceOutput()
        self.transport = transport or PythonSessionTransport()
        self.writer = writer
        self.turn_controller = turn_controller or TurnController(EnergyVAD())
        self.push_to_talk_commands = push_to_talk_commands
        self.echo = echo

    async def run(
        self,
        url: str,
        input_device: int | None,
        output_device: int | None,
        push_to_talk: bool,
    ) -> None:
        await self.input_source.open(input_device)
        socket = None
        tasks: list[asyncio.Task] = []
        done = asyncio.Event()
        state = {
            "response_id": None,
            "output_format": None,
            "confirmed": False,
            "confirmed_response": None,
        }
        try:
            async with self.transport.connect(url) as socket:
                await socket.send_json(
                    {
                        "type": "session.start",
                        "version": "voice.booking.v1",
                        "audio_format": {
                            "encoding": "pcm_s16le",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    }
                )
                tasks = [
                    asyncio.create_task(
                        self._receive(socket, output_device, state, done)
                    ),
                    asyncio.create_task(self._capture(socket, push_to_talk)),
                    asyncio.create_task(self._playback_ack(socket, state, done)),
                    asyncio.create_task(done.wait()),
                ]
                finished, _ = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in finished:
                    if task is not tasks[-1]:
                        task.result()
                if done.is_set():
                    await socket.close()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.input_source.close()
            await self.output.close()
            if socket is not None and not getattr(socket, "closed", False):
                with suppress(Exception):
                    await socket.close()

    async def _capture(self, socket, push_to_talk: bool) -> None:
        if push_to_talk:
            await self._capture_push_to_talk(socket)
            return
        turn_id: str | None = None
        try:
            async for frame in self.input_source.frames():
                for event in self.turn_controller.feed(frame):
                    if event.kind == "start":
                        self.output.clear()
                        await socket.send_json({"type": "response.interrupt"})
                        turn_id = str(uuid4())
                        await socket.send_json(
                            {"type": "input_audio.start", "turn_id": turn_id}
                        )
                    elif event.kind == "frame" and event.pcm is not None:
                        await socket.send_bytes(event.pcm)
                    elif event.kind == "end" and turn_id:
                        await socket.send_json(
                            {"type": "input_audio.end", "turn_id": turn_id}
                        )
                        turn_id = None
        except AudioInputOverflow:
            self.turn_controller.overflow()
            if turn_id:
                await socket.send_json(
                    {"type": "input_audio.end", "turn_id": turn_id}
                )
            self.echo("Audio đầu vào bị tràn; vui lòng nói lại.")

    async def _capture_push_to_talk(self, socket) -> None:
        frames = self.input_source.frames().__aiter__()
        commands = (
            self.push_to_talk_commands
            if self.push_to_talk_commands is not None
            else self._terminal_push_to_talk_commands()
        ).__aiter__()
        frame_task = asyncio.create_task(anext(frames))
        command_task = asyncio.create_task(anext(commands))
        turn_id: str | None = None
        try:
            while True:
                finished, _ = await asyncio.wait(
                    {frame_task, command_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if command_task in finished:
                    command = command_task.result()
                    command_task = asyncio.create_task(anext(commands))
                    if command == "start" and turn_id is None:
                        self.output.clear()
                        await socket.send_json({"type": "response.interrupt"})
                        turn_id = str(uuid4())
                        await socket.send_json(
                            {"type": "input_audio.start", "turn_id": turn_id}
                        )
                    elif command == "end" and turn_id is not None:
                        await socket.send_json(
                            {"type": "input_audio.end", "turn_id": turn_id}
                        )
                        turn_id = None
                if frame_task in finished:
                    frame = frame_task.result()
                    frame_task = asyncio.create_task(anext(frames))
                    if turn_id is not None:
                        await socket.send_bytes(frame)
        finally:
            for task in (frame_task, command_task):
                task.cancel()
            await asyncio.gather(frame_task, command_task, return_exceptions=True)

    async def _terminal_push_to_talk_commands(self) -> AsyncIterator[str]:
        active = False
        while True:
            action = "kết thúc" if active else "bắt đầu"
            self.echo(f"Nhấn Enter để {action} lượt nói.")
            await self._read_terminal_line()
            active = not active
            yield "start" if active else "end"

    @staticmethod
    async def _read_terminal_line() -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def ready() -> None:
            sys.stdin.readline()
            if not future.done():
                future.set_result(None)

        try:
            loop.add_reader(sys.stdin.fileno(), ready)
        except (AttributeError, NotImplementedError):
            await asyncio.to_thread(sys.stdin.readline)
            return
        try:
            await future
        finally:
            loop.remove_reader(sys.stdin.fileno())

    async def _receive(self, socket, output_device, state, done) -> None:
        while not done.is_set():
            event = await socket.receive()
            if isinstance(event, bytes):
                response_id = state["response_id"]
                audio_format = state["output_format"]
                if response_id and audio_format:
                    self.output.enqueue(
                        response_id,
                        AudioPacket(format=audio_format, data=event),
                    )
                continue
            event_type = event.get("type")
            if event_type == "assistant.audio.start":
                audio_format = AudioFormat.model_validate(event["format"])
                if state["output_format"] is None:
                    await self.output.open(audio_format, output_device)
                elif state["output_format"] != audio_format:
                    raise RuntimeError("TTS audio format changed within the session")
                state["output_format"] = audio_format
                state["response_id"] = event["response_id"]
            elif event_type == "assistant.audio.end":
                response_id = event["response_id"]
                self.output.end_response(response_id)
                if state["confirmed"]:
                    state["confirmed_response"] = response_id
            elif event_type == "assistant.response.interrupted":
                self.output.clear()
                state["response_id"] = None
            elif event_type == "booking.confirmed":
                state["confirmed"] = True
                try:
                    path = self.writer.write(event["payload"])
                except (ResultWriteError, ResultConflict, ValueError) as exc:
                    self.echo(f"Lỗi lưu kết quả: {exc}")
                else:
                    self.echo(f"Đã lưu kết quả: {path}")
            elif event_type == "error":
                self.echo(f"Lỗi {event.get('code')}: {event.get('message')}")

    async def _playback_ack(self, socket, state, done) -> None:
        async for response_id in self.output.finished():
            await socket.send_json(
                {
                    "type": "assistant.playback.finished",
                    "response_id": response_id,
                }
            )
            if response_id == state["confirmed_response"]:
                done.set()
                return
