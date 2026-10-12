from pathlib import Path

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.models import (
    EngineInfo,
    RuntimeProfile,
    VoiceInfo,
    VoiceRef,
)


def test_voice_reference_preserves_provider_namespace() -> None:
    reference = VoiceRef(
        provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234"
    )

    decoded = VoiceRef.decode(reference.encode())

    assert decoded.provider_voice_id == "abcd1234"
    assert decoded == reference
    assert VoiceRef(
        provider_id="a", kind="clone", provider_voice_id="abcd1234"
    ).encode() != VoiceRef(
        provider_id="b", kind="clone", provider_voice_id="abcd1234"
    ).encode()


def test_profile_id_is_opaque() -> None:
    voice = VoiceInfo(
        reference=VoiceRef(
            provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234"
        ),
        name="Giọng Nam",
    )

    assert voice.reference.provider_voice_id == "abcd1234"


def test_secret_is_not_serialized(tmp_path: Path) -> None:
    settings = Settings(
        voicestudio_api_key="top-secret",
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "outputs",
    )

    assert "top-secret" not in settings.model_dump_json()
    assert settings.safe_dict()["voicestudio_api_key"] == "**********"


def test_defaults_do_not_require_model_imports(tmp_path: Path) -> None:
    settings = Settings(state_dir=tmp_path / "state", output_dir=tmp_path / "out")
    profile = RuntimeProfile.default()
    engine = EngineInfo(id="unknown", family="asr", available=None)

    assert settings.voicestudio_url == "http://127.0.0.1:3900"
    assert settings.connect_timeout_seconds == 10
    assert settings.rest_timeout_seconds == 30
    assert settings.inference_inactivity_timeout_seconds == 60
    assert settings.max_turn_seconds == 30
    assert profile.tts.engine_id == "omnivoice"
    assert profile.language == "vi"
    assert profile.exact_model is False
    assert engine.available is None
