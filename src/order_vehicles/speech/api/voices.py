import wave
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel

from order_vehicles.speech.domain.models import CloneRequest, VoiceFilter, VoiceRef
from order_vehicles.speech.domain.errors import SpeechError

from .dependencies import require_management_token, service

router = APIRouter(prefix="/v1/speech/voices", tags=["speech-voices"])


class PreviewBody(BaseModel):
    text: str


class VoicePatch(BaseModel):
    name: str | None = None
    description: str | None = None


class ArchetypeBody(BaseModel):
    archetype_id: str


@router.get("")
async def list_voices(
    request: Request,
    language: str | None = None,
    kind: str | None = None,
    query: str | None = None,
):
    return await service(request, "voices").list_voices(
        VoiceFilter(language=language, kind=kind, query=query)
    )


@router.get("/archetypes")
async def list_archetypes(
    request: Request, language: str | None = None, query: str | None = None
):
    return await service(request, "voices").list_archetypes(
        VoiceFilter(language=language, query=query)
    )


@router.get("/presets")
async def list_presets(request: Request, engine_id: str):
    return service(request, "voices").list_presets(engine_id)


@router.post("/clone", status_code=201, dependencies=[Depends(require_management_token)])
async def clone_voice(
    request: Request,
    ref_audio: UploadFile = File(...),
    name: str = Form(...),
    ref_text: str = Form(""),
    language: str = Form("vi"),
    description: str | None = Form(None),
):
    content = await ref_audio.read(20 * 1024 * 1024 + 1)
    metadata = CloneRequest(
        name=name,
        ref_text=ref_text,
        language=language,
        filename=ref_audio.filename or "reference.wav",
        description=description,
    )
    return await service(request, "voices").create_clone(content, metadata)


@router.post("/archetypes/{archetype_id}/use", dependencies=[Depends(require_management_token)])
async def use_archetype(archetype_id: str, request: Request):
    return await service(request, "voices").use_archetype(archetype_id)


@router.post("/{encoded_ref}/preview", dependencies=[Depends(require_management_token)])
async def preview_voice(encoded_ref: str, body: PreviewBody, request: Request):
    ref = VoiceRef.decode(encoded_ref)
    profile = service(request, "repository").get()
    packets = []
    size = 0
    async for packet in service(request, "management").preview_voice(
        ref, body.text, profile.speech_config()
    ):
        size += len(packet.data)
        if size > 64 * 1024 * 1024:
            raise SpeechError("AUDIO_TOO_LARGE", "Preview exceeded the response size limit")
        packets.append(packet)
    if not packets:
        return Response(status_code=503)
    stream = BytesIO()
    audio_format = packets[0].format
    with wave.open(stream, "wb") as output:
        output.setnchannels(audio_format.channels)
        output.setsampwidth(2)
        output.setframerate(audio_format.sample_rate)
        output.writeframes(b"".join(packet.data for packet in packets))
    return Response(
        stream.getvalue(),
        media_type="audio/wav",
        headers={"X-Sample-Rate": str(audio_format.sample_rate)},
    )


@router.post("/from-archetype", dependencies=[Depends(require_management_token)])
async def from_archetype(body: ArchetypeBody, request: Request):
    return await service(request, "voices").use_archetype(body.archetype_id)


@router.patch("/{encoded_ref}", dependencies=[Depends(require_management_token)])
async def update_voice(encoded_ref: str, body: VoicePatch, request: Request):
    return await service(request, "voices").update_voice(
        VoiceRef.decode(encoded_ref), body.model_dump(exclude_none=True)
    )


@router.delete("/{encoded_ref}", status_code=204, dependencies=[Depends(require_management_token)])
async def delete_voice(encoded_ref: str, request: Request):
    await service(request, "management").delete_voice(VoiceRef.decode(encoded_ref))
    return Response(status_code=204)
