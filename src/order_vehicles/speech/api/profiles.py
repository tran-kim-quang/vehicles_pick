from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from order_vehicles.speech.domain.models import RuntimeProfile

from .dependencies import require_management_token, service

router = APIRouter(prefix="/v1/speech/profile", tags=["speech-profile"])
contract_router = APIRouter(prefix="/v1/speech/runtime-profile", tags=["speech-profile"])


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


contract_router.add_api_route("", get_profile, methods=["GET"])
contract_router.add_api_route("", put_profile, methods=["PUT"], dependencies=[Depends(require_management_token)])
