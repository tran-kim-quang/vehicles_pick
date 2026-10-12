import asyncio
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Protocol
from uuid import UUID, uuid4

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import ManagementJob, ModelInfo


class JobModelManager(Protocol):
    async def install_model(self, repo_id: str) -> str: ...
    async def watch_installation(self): ...
    async def list_models(self) -> list[ModelInfo]: ...
    async def cancel_install(self, repo_id: str) -> None: ...


class ModelJobService:
    def __init__(
        self,
        manager: JobModelManager,
        *,
        tracking_timeout_seconds: float = 1800,
        clock=monotonic,
    ) -> None:
        self.manager = manager
        self.tracking_timeout_seconds = tracking_timeout_seconds
        self.clock = clock
        self._jobs: dict[UUID, ManagementJob] = {}
        self._active_by_repo: dict[str, UUID] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self, repo_id: str) -> ManagementJob:
        async with self._lock:
            active_id = self._active_by_repo.get(repo_id)
            if active_id and self._jobs[active_id].status in {
                "pending", "running", "cancel_requested"
            }:
                return self._jobs[active_id]
            await self.manager.install_model(repo_id)
            now = datetime.now(timezone.utc)
            job = ManagementJob(
                id=uuid4(),
                repo_id=repo_id,
                status="running",
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.id] = job
            self._active_by_repo[repo_id] = job.id
            self._tasks[job.id] = asyncio.create_task(self._track(job.id))
            return job

    def get(self, job_id: UUID | str) -> ManagementJob:
        key = UUID(str(job_id))
        try:
            return self._jobs[key]
        except KeyError as exc:
            raise SpeechError("JOB_NOT_FOUND", f"Unknown model job {job_id}") from exc

    async def cancel(self, job_id: UUID | str) -> ManagementJob:
        job = self.get(job_id)
        if job.status not in {"pending", "running", "cancel_requested"}:
            return job
        await self.manager.cancel_install(job.repo_id)
        updated = self._replace(job, status="cancel_requested", message="Cancellation requested")
        self._jobs[job.id] = updated
        return updated

    async def wait(self, job_id: UUID | str) -> ManagementJob:
        key = UUID(str(job_id))
        task = self._tasks.get(key)
        if task:
            await task
        return self.get(key)

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _track(self, job_id: UUID) -> None:
        job = self._jobs[job_id]
        try:
            async with asyncio.timeout(self.tracking_timeout_seconds):
                last_error: Exception | None = None
                last_state = "unknown"
                for attempt in range(3):
                    terminal = None
                    try:
                        terminal = await self._watch_once(job_id, job.repo_id)
                    except Exception as exc:
                        last_error = exc
                    if terminal == "install_cancelled":
                        current = self._jobs[job_id]
                        self._jobs[job_id] = self._replace(current, status="cancelled", message="Cancelled")
                        return
                    try:
                        last_state = await self._catalog_state(job.repo_id)
                    except Exception as exc:
                        last_error = exc
                        if attempt < 2:
                            await asyncio.sleep(0)
                            continue
                        break
                    if last_state == "installed":
                        current = self._jobs[job_id]
                        self._jobs[job_id] = self._replace(current, status="completed", progress=1.0, message="Installed")
                        return
                    if terminal in {"install_error", "failed"}:
                        break
                    if attempt < 2:
                        await asyncio.sleep(0)
                current = self._jobs[job_id]
                final_status = "failed" if last_state == "incomplete" else "unknown"
                self._jobs[job_id] = self._replace(
                    current,
                    status=final_status,
                    message=f"Installation status is {last_state}; reconciliation exhausted"
                    + (f" ({type(last_error).__name__})" if last_error else ""),
                )
        except TimeoutError:
            current = self._jobs[job_id]
            self._jobs[job_id] = self._replace(
                current,
                status="unknown",
                message="Installation tracking timed out; upstream completion is unknown",
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._active_by_repo.pop(job.repo_id, None)

    async def _watch_once(self, job_id: UUID, repo_id: str) -> str | None:
        async for event in self.manager.watch_installation():
            if event.get("repo_id") not in {None, repo_id}:
                continue
            progress = event.get("progress", event.get("pct"))
            if progress is not None:
                current = self._jobs[job_id]
                self._jobs[job_id] = self._replace(
                    current, progress=float(progress), message=event.get("message")
                )
            phase = event.get("phase")
            status = event.get("status")
            if status in {"completed", "failed", "cancelled"} or phase in {
                "install_done", "install_error", "install_cancelled"
            }:
                return str(phase or status)
        return None

    async def _catalog_state(self, repo_id: str) -> str:
        models = await self.manager.list_models()
        model = next((item for item in models if item.repo_id == repo_id), None)
        return model.installation_state if model else "unknown"

    async def _refresh_terminal(self, job_id: UUID) -> None:
        current = self._jobs[job_id]
        models = await self.manager.list_models()
        model = next((item for item in models if item.repo_id == current.repo_id), None)
        if model and model.installation_state == "installed":
            self._jobs[job_id] = self._replace(
                current, status="completed", progress=1.0, message="Installed"
            )
        else:
            state = model.installation_state if model else "unknown"
            self._jobs[job_id] = self._replace(
                current,
                status="failed",
                message=f"Weights are {state}; installation is not complete",
            )

    @staticmethod
    def _replace(job: ManagementJob, **changes: Any) -> ManagementJob:
        return job.model_copy(
            update={"updated_at": datetime.now(timezone.utc), **changes}
        )
