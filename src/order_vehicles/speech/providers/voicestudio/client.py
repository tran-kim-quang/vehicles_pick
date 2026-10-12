import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError


class VoiceStudioClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        websocket_connector: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        token = settings.voicestudio_api_key.get_secret_value()
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        timeout = httpx.Timeout(
            settings.rest_timeout_seconds, connect=settings.connect_timeout_seconds
        )
        self.http = httpx.AsyncClient(
            base_url=settings.voicestudio_url,
            headers=self.headers,
            timeout=timeout,
            transport=transport,
        )
        self.websocket_connector = websocket_connector or connect

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SpeechError("UPSTREAM_TIMEOUT", "VoiceStudio request timed out", True) from exc
        except httpx.HTTPError as exc:
            raise SpeechError("UPSTREAM_UNAVAILABLE", "VoiceStudio is unavailable", True) from exc
        if response.is_error:
            message = self._error_message(response)
            code = {
                401: "UPSTREAM_UNAUTHORIZED",
                403: "UPSTREAM_FORBIDDEN",
                404: "UPSTREAM_NOT_FOUND",
                409: "UPSTREAM_CONFLICT",
                422: "UPSTREAM_UNSUPPORTED",
            }.get(response.status_code, "UPSTREAM_ERROR")
            raise SpeechError(code, message, response.status_code >= 500)
        return response

    async def iter_sse(self, path: str) -> AsyncIterator[dict[str, Any]]:
        try:
            async with self.http.stream("GET", path, headers={"Accept": "text/event-stream"}) as response:
                if response.is_error:
                    await response.aread()
                    raise SpeechError(
                        "UPSTREAM_ERROR", self._error_message(response), response.status_code >= 500
                    )
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk.replace("\r\n", "\n")
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        data = "\n".join(
                            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
                        )
                        if data and data != "[DONE]":
                            yield json.loads(data)
        except SpeechError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise SpeechError("UPSTREAM_STREAM_ERROR", "Invalid VoiceStudio event stream", True) from exc

    def websocket_url(self, path: str) -> str:
        base = urlsplit(self.settings.voicestudio_url)
        scheme = "wss" if base.scheme == "https" else "ws"
        return urlunsplit((scheme, base.netloc, path, "", ""))

    async def aclose(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        raw = response.content[:16_384]
        try:
            payload = json.loads(raw)
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, str):
                return detail
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        text = raw.decode("utf-8", errors="replace").strip()
        return text or f"VoiceStudio returned HTTP {response.status_code}"
