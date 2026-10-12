from order_vehicles.speech.domain.models import VoiceRef


_VERIFIED_PRESETS: dict[str, tuple[str, ...]] = {
    "omnivoice": ("default",),
}


def verified_presets(engine_id: str) -> list[VoiceRef]:
    return [
        VoiceRef(
            provider_id="voicestudio", kind="preset", provider_voice_id=voice_id
        )
        for voice_id in _VERIFIED_PRESETS.get(engine_id, ())
    ]
