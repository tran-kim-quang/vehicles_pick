from collections.abc import AsyncIterator
from typing import Protocol


class LLMAdapter(Protocol):
    async def extract(
        self, transcript: str, context: dict, retry: bool = False
    ) -> object: ...

    async def stream_reply(self, instruction: str, context: dict) -> AsyncIterator[str]: ...
