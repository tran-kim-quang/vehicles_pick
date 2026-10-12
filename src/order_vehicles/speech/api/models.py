from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from .dependencies import require_management_token, service

router = APIRouter(prefix="/v1/speech/models", tags=["speech-models"])
contract_router = APIRouter(prefix="/v1/speech", tags=["speech-models"])


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


@contract_router.get("/engines")
async def list_engines(request: Request):
    return await service(request, "models").list_engines()


@contract_router.post("/model-jobs", status_code=202, dependencies=[Depends(require_management_token)])
async def create_model_job(body: InstallBody, request: Request):
    return await service(request, "jobs").start(body.repo_id)


@contract_router.get("/jobs/{job_id}")
async def contract_job_status(job_id: UUID, request: Request):
    return service(request, "jobs").get(job_id)


@contract_router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_management_token)])
async def contract_cancel_job(job_id: UUID, request: Request):
    return await service(request, "jobs").cancel(job_id)
