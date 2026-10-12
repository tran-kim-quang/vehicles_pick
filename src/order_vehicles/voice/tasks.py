import asyncio
from collections.abc import Awaitable
from contextlib import suppress


class ResponseTasks:
    """Own the currently audible response and cancel it at a turn boundary."""

    def __init__(self) -> None:
        self.response_id: str | None = None
        self.task: asyncio.Task | None = None

    async def start(self, response_id: str, work: Awaitable[None]) -> None:
        await self.interrupt()
        self.response_id = response_id
        self.task = asyncio.create_task(work)

    async def interrupt(self) -> None:
        task = self.task
        self.task = None
        self.response_id = None
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        elif task:
            with suppress(Exception):
                task.result()

    async def close(self) -> None:
        await self.interrupt()

    @property
    def active_count(self) -> int:
        return int(self.task is not None and not self.task.done())
