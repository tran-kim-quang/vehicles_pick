from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import (
    AudioPacket,
    CloneRequest,
    SpeechConfig,
    VoiceFilter,
    VoiceInfo,
    VoiceRef,
)

from .client import VoiceStudioClient


class StreamingTTS:
    async def stream_synthesize(
        self, text: str, config: SpeechConfig
    ) -> AsyncIterator[AudioPacket]: ...


class VoiceStudioVoiceLibrary:
    MAX_UPLOAD_BYTES = 20 * 1024 * 1024
    ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".m4a"}

    def __init__(
        self,
        client: VoiceStudioClient,
        tts: StreamingTTS,
        *,
        clone_capability: Callable[[str], Awaitable[bool | None]],
    ) -> None:
        self.client = client
        self.tts = tts
        self.clone_capability = clone_capability

    async def list_voices(self, filter: VoiceFilter) -> list[VoiceInfo]:
        params = {k: v for k, v in filter.model_dump().items() if v is not None}
        profiles_response = await self.client.request("GET", "/profiles", params=params)
        profiles_payload = profiles_response.json()
        profiles = profiles_payload.get("profiles", profiles_payload if isinstance(profiles_payload, list) else [])
        voices = [self._profile(item) for item in profiles]

        aliases_response = await self.client.request("GET", "/v1/audio/voices")
        aliases_payload = aliases_response.json()
        aliases = aliases_payload.get("voices", aliases_payload if isinstance(aliases_payload, list) else [])
        for item in aliases:
            voice_id = str(item.get("id") or item.get("voice_id") or item.get("name"))
            voices.append(
                VoiceInfo(
                    reference=VoiceRef(
                        provider_id="voicestudio",
                        kind="default",
                        provider_voice_id=voice_id,
                    ),
                    name=str(item.get("name") or voice_id),
                    language=item.get("language"),
                    default_alias=True,
                )
            )
        return [voice for voice in voices if self._matches(voice, filter)]

    async def create_clone(
        self, reference: bytes, metadata: CloneRequest
    ) -> VoiceInfo:
        capability = await self.clone_capability("voice_clone")
        if capability is not True:
            code = "CLONE_UNSUPPORTED" if capability is False else "CAPABILITY_UNVERIFIED"
            raise SpeechError(code, "Voice cloning capability is not verified for this engine")
        extension = Path(metadata.filename).suffix.lower()
        if extension not in self.ALLOWED_EXTENSIONS:
            raise SpeechError("UNSUPPORTED_AUDIO", f"Unsupported reference extension {extension!r}")
        if len(reference) > self.MAX_UPLOAD_BYTES:
            raise SpeechError("UPLOAD_TOO_LARGE", "Reference audio exceeds 20 MiB")
        response = await self.client.request(
            "POST",
            "/profiles",
            files={"ref_audio": (Path(metadata.filename).name, reference, self._mime(extension))},
            data={
                "name": metadata.name,
                "kind": "clone",
                "ref_text": metadata.ref_text,
                "language": metadata.language,
                "description": metadata.description or "",
            },
        )
        return self._profile(response.json(), default_kind="clone")

    async def update_voice(self, ref: VoiceRef, patch: dict[str, Any]) -> VoiceInfo:
        self._saved_profile(ref)
        response = await self.client.request(
            "PATCH", f"/profiles/{ref.provider_voice_id}", json=patch
        )
        return self._profile(response.json(), default_kind=ref.kind)

    async def preview_voice(
        self, ref: VoiceRef, text: str, config: SpeechConfig
    ) -> AsyncIterator[AudioPacket]:
        if ref.provider_id != "voicestudio":
            raise SpeechError("WRONG_PROVIDER", "Voice belongs to another provider")
        preview_config = config.model_copy(update={"voice_ref": ref})
        async for packet in self.tts.stream_synthesize(text, preview_config):
            yield packet

    async def list_archetypes(self, filter: VoiceFilter) -> list[dict[str, Any]]:
        page = 1
        result: list[dict[str, Any]] = []
        while True:
            response = await self.client.request(
                "GET", "/archetypes", params={"page": page, "per_page": 100}
            )
            payload = response.json()
            items = payload.get("items", [])
            result.extend(
                item
                for item in items
                if (not filter.language or item.get("language") == filter.language)
                and (not filter.query or filter.query.casefold() in str(item.get("name", "")).casefold())
            )
            if not payload.get("has_more"):
                return result
            page += 1

    async def use_archetype(self, id: str) -> VoiceInfo:
        response = await self.client.request("POST", f"/archetypes/{id}/use")
        payload = response.json()
        profile_id = payload.get("profile_id")
        if not profile_id:
            raise SpeechError("UPSTREAM_PROTOCOL", "Archetype did not create a saved profile")
        return VoiceInfo(
            reference=VoiceRef(
                provider_id="voicestudio",
                kind="design",
                provider_voice_id=str(profile_id),
            ),
            name=str(payload.get("name") or id),
            language=payload.get("language"),
            description=payload.get("description"),
        )

    async def delete_voice(self, ref: VoiceRef) -> None:
        self._saved_profile(ref)
        await self.client.request("DELETE", f"/profiles/{ref.provider_voice_id}")

    @staticmethod
    def _saved_profile(ref: VoiceRef) -> None:
        if ref.provider_id != "voicestudio" or ref.kind not in {"clone", "design"}:
            raise SpeechError("UNSUPPORTED_VOICE_MUTATION", "Only saved VoiceStudio profiles can be changed")

    @staticmethod
    def _profile(item: dict[str, Any], *, default_kind: str = "clone") -> VoiceInfo:
        profile_id = item.get("id") or item.get("profile_id")
        if not profile_id:
            raise SpeechError("UPSTREAM_PROTOCOL", "Voice profile is missing its opaque ID")
        kind = str(item.get("kind") or default_kind)
        if kind not in {"clone", "design"}:
            kind = default_kind
        return VoiceInfo(
            reference=VoiceRef(
                provider_id="voicestudio",
                kind=kind,
                provider_voice_id=str(profile_id),
            ),
            name=str(item.get("name") or profile_id),
            language=item.get("language"),
            description=item.get("description"),
            capabilities=item.get("capabilities", {}),
        )

    @staticmethod
    def _matches(voice: VoiceInfo, filter: VoiceFilter) -> bool:
        return (
            (not filter.language or voice.language == filter.language)
            and (not filter.kind or voice.reference.kind == filter.kind)
            and (
                not filter.query
                or filter.query.casefold() in voice.name.casefold()
            )
        )

    @staticmethod
    def _mime(extension: str) -> str:
        return {
            ".wav": "audio/wav",
            ".flac": "audio/flac",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4",
        }[extension]
