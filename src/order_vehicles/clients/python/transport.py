import json
from contextlib import asynccontextmanager

from websockets.asyncio.client import connect


class WebSocketSessionSocket:
    def __init__(self, websocket) -> None:
        self.websocket = websocket

    async def receive(self) -> dict | bytes:
        message = await self.websocket.recv()
        return message if isinstance(message, bytes) else json.loads(message)

    async def send_json(self, value: dict) -> None:
        await self.websocket.send(json.dumps(value, ensure_ascii=False))

    async def send_bytes(self, value: bytes) -> None:
        await self.websocket.send(value)

    async def close(self, code: int = 1000) -> None:
        await self.websocket.close(code=code)


class PythonSessionTransport:
    def __init__(self, connector=connect) -> None:
        self.connector = connector

    @asynccontextmanager
    async def connect(self, url: str):
        async with self.connector(url) as websocket:
            yield WebSocketSessionSocket(websocket)
