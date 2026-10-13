import json
from collections.abc import AsyncIterator

import httpx

from .prompts import extraction_messages


class ChatCompletionsLLMAdapter:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        client=None,
        structured_output: bool = False,
        timeout: float = 30,
    ) -> None:
        if not base_url or not model:
            raise ValueError("LLM base URL and model are required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.structured_output = structured_output
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.AsyncClient(headers=headers, timeout=timeout)

    async def extract(self, transcript: str, context: dict, retry: bool = False):
        body = {
            "model": self.model,
            "messages": extraction_messages(transcript, context, retry),
            "stream": False,
            "temperature": 0,
        }
        if self.structured_output:
            body["response_format"] = {"type": "json_object"}
        response = await self.client.post(f"{self.base_url}/chat/completions", json=body)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    async def stream_reply(self, instruction: str, context: dict) -> AsyncIterator[str]:
        data = {key: value for key, value in context.items() if key != "user_transcript"}
        messages = [
            {"role": "system", "content": instruction},
            {"role": "system", "content": "Dữ liệu phiên: " + json.dumps(data, ensure_ascii=False, default=str)},
        ]
        if "user_transcript" in context:
            messages.append({"role": "user", "content": context["user_transcript"]})
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        async with self.client.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
            response.raise_for_status()
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk.replace("\r\n", "\n")
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    data = "".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                    if not data or data == "[DONE]":
                        continue
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
