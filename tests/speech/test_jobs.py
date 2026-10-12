import asyncio

import pytest

from order_vehicles.speech.application.jobs import ModelJobService
from order_vehicles.speech.domain.models import ModelInfo


class FakeManager:
    def __init__(self, states, events=()) -> None:
        self.states = list(states)
        self.events = list(events)
        self.installs = []
        self.cancels = []
        self.block = False
        self.release = asyncio.Event()

    async def install_model(self, repo_id: str) -> str:
        self.installs.append(repo_id)
        return repo_id

    async def watch_installation(self):
        for event in self.events:
            yield event
        if self.block:
            await self.release.wait()

    async def list_models(self):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return [ModelInfo(repo_id="known/model", installation_state=state)]

    async def cancel_install(self, repo_id: str) -> None:
        self.cancels.append(repo_id)


@pytest.mark.asyncio
async def test_cancel_is_requested_until_upstream_finishes() -> None:
    manager = FakeManager(["available"])
    manager.block = True
    service = ModelJobService(manager, tracking_timeout_seconds=2)
    started = await service.start("known/model")

    job = await service.cancel(started.id)

    assert job.status == "cancel_requested"
    assert manager.cancels == ["known/model"]
    manager.release.set()
    await service.close()


@pytest.mark.asyncio
async def test_sse_reconnect_refreshes_catalog() -> None:
    manager = FakeManager(["installed"])
    service = ModelJobService(manager)

    started = await service.start("known/model")
    job = await service.wait(started.id)

    assert job.status == "completed"
    assert (await manager.list_models())[0].installation_state == "installed"


@pytest.mark.asyncio
async def test_completion_requires_complete_weights() -> None:
    manager = FakeManager(["incomplete"])
    service = ModelJobService(manager)

    started = await service.start("known/model")
    job = await service.wait(started.id)

    assert job.status == "failed"
    assert "incomplete" in (job.message or "")


@pytest.mark.asyncio
async def test_duplicate_install_reuses_running_job() -> None:
    manager = FakeManager(["available"])
    manager.block = True
    service = ModelJobService(manager, tracking_timeout_seconds=2)

    first = await service.start("known/model")
    second = await service.start("known/model")

    assert first.id == second.id
    assert manager.installs == ["known/model"]
    manager.release.set()
    await service.close()
