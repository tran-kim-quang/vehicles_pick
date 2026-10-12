from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from order_vehicles.speech.domain.models import RuntimeProfile

from .dependencies import require_management_token, service

router = APIRouter(prefix="/v1/speech/profile", tags=["speech-profile"])


class ProfileUpdate(BaseModel):
    profile: RuntimeProfile
    expected_revision: int


@router.get("")
async def get_profile(request: Request):
    return service(request, "repository").get()


@router.put("", dependencies=[Depends(require_management_token)])
async def put_profile(body: ProfileUpdate, request: Request):
    return await service(request, "management").update_profile(
        body.profile, body.expected_revision
    )
