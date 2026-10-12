from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from .dependencies import require_management_token, service

router = APIRouter(prefix="/v1/speech/models", tags=["speech-models"])


class InstallBody(BaseModel):
    repo_id: str


@router.get("")
async def list_models(request: Request):
    return await service(request, "models").list_models()


@router.post("/install", status_code=202, dependencies=[Depends(require_management_token)])
async def install(body: InstallBody, request: Request, response: Response):
    job = await service(request, "jobs").start(body.repo_id)
    response.headers["Location"] = f"/v1/speech/models/jobs/{job.id}"
    return job


@router.get("/jobs/{job_id}")
async def job_status(job_id: UUID, request: Request):
    return service(request, "jobs").get(job_id)


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_management_token)])
async def cancel_job(job_id: UUID, request: Request):
    return await service(request, "jobs").cancel(job_id)
