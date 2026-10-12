import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter()


class FastAPISessionSocket:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def receive(self) -> dict | bytes:
        message = await self.websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if message.get("bytes") is not None:
            return message["bytes"]
        return json.loads(message.get("text") or "{}")

    async def send_json(self, value: dict) -> None:
        await self.websocket.send_json(value)

    async def send_bytes(self, value: bytes) -> None:
        await self.websocket.send_bytes(value)

    async def close(self, code: int = 1000) -> None:
        await self.websocket.close(code=code)


@router.websocket("/v1/voice/sessions")
async def voice_session(websocket: WebSocket) -> None:
    await websocket.accept()
    service = websocket.app.state.services.get("voice_sessions")
    if service is None:
        await websocket.send_json(
            {
                "type": "error",
                "code": "VOICE_NOT_CONFIGURED",
                "message": "Configure OV_LLM_BASE_URL and OV_LLM_MODEL",
                "retryable": False,
            }
        )
        await websocket.close(code=1013)
        return
    await service.run(FastAPISessionSocket(websocket))
