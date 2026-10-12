import json
from collections.abc import Iterable
from typing import Any


class ScriptedWebSocket:
    def __init__(self, frames: Iterable[Any], owner: "ScriptedConnector") -> None:
        self.frames = iter(frames)
        self.owner = owner
        self.sent: list[Any] = []
        self.closed = False

    async def __aenter__(self) -> "ScriptedWebSocket":
        self.owner.open_connections += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def send(self, value: Any) -> None:
        self.sent.append(value)

    async def recv(self) -> Any:
        item = next(self.frames)
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item) if isinstance(item, dict) else item

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.owner.open_connections -= 1


class ScriptedConnector:
    def __init__(self, scripts: list[list[Any]]) -> None:
        self.scripts = list(scripts)
        self.connections: list[ScriptedWebSocket] = []
        self.urls: list[str] = []
        self.open_connections = 0

    def __call__(self, url: str, **_: object) -> ScriptedWebSocket:
        self.urls.append(url)
        socket = ScriptedWebSocket(self.scripts.pop(0), self)
        self.connections.append(socket)
        return socket


async def byte_frames(*frames: bytes):
    for frame in frames:
        yield frame
