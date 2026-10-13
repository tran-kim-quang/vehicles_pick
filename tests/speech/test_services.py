import pytest

from order_vehicles.speech.application.leases import ResourceLeases
from order_vehicles.speech.application.management import SpeechManagementService
from order_vehicles.speech.application.profiles import JsonRuntimeProfileRepository
from order_vehicles.speech.application.runtime import SpeechRuntimeService
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import ModelSelection, RuntimeProfile, VoiceRef


class FakeRecognizer:
    async def stream_transcribe(self, frames, config):
        if False:
            yield None


class FakeSynthesizer:
    async def stream_synthesize(self, text, config):
        if False:
            yield None


class FakeModelManager:
    def __init__(self, selection=None) -> None:
        self.selection = selection or {}

    async def select_model(self, selection):
        return self.selection


@pytest.mark.asyncio
async def test_cloud_tts_override_preserves_voicestudio_stt_and_saved_profile(tmp_path):
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    recognizer, cloud = FakeRecognizer(), FakeSynthesizer()
    stored = repository.get()
    svc = SpeechRuntimeService(
        repository, ResourceLeases(), {"vieneu": (recognizer, cloud)},
        readiness=lambda _: True,
        profile_overrides={
            "voice_ref": VoiceRef(provider_id="vieneu", kind="preset", provider_voice_id="Ngọc Lan"),
            "tts": ModelSelection(engine_id="vieneu", model_id="vieneu-v4", purpose="tts"),
        },
    )
    async with svc.open() as runtime:
        assert runtime.recognizer is recognizer and runtime.synthesizer is cloud
        assert runtime.profile.stt == stored.stt
        assert runtime.profile.tts.engine_id == "vieneu"
    assert repository.get() == stored


@pytest.mark.asyncio
async def test_session_keeps_snapshot_after_default_change(tmp_path) -> None:
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    service = SpeechRuntimeService(
        repository,
        ResourceLeases(),
        {"voicestudio": (FakeRecognizer(), FakeSynthesizer())},
        readiness=lambda _: True,
    )

    async with service.open() as runtime:
        repository.save(
            repository.get().model_copy(update={"language": "en"}), expected_revision=1
        )
        active_snapshot = runtime.profile

    assert active_snapshot.revision == 1 and repository.get().revision == 2


@pytest.mark.asyncio
async def test_env_override_mismatch_is_conflict(tmp_path) -> None:
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    manager = FakeModelManager(
        {"backend_id": "other", "env_override": True, "model_id": None}
    )
    service = SpeechManagementService(repository, ResourceLeases(), manager, voices=None)
    changed = repository.get().model_copy(
        update={"tts": ModelSelection(engine_id="omnivoice", purpose="tts")}
    )

    with pytest.raises(SpeechError) as caught:
        await service.update_profile(changed, expected_revision=1)

    assert caught.value.code == "MODEL_MISMATCH"
    assert repository.get().revision == 1


@pytest.mark.asyncio
async def test_configured_model_without_weights_is_not_ready(tmp_path) -> None:
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    profile = repository.get().model_copy(
        update={"tts": ModelSelection(engine_id="omnivoice", model_id="missing", purpose="tts")}
    )
    service = SpeechRuntimeService(
        repository,
        ResourceLeases(),
        {"voicestudio": (FakeRecognizer(), FakeSynthesizer())},
        readiness=lambda _: False,
    )

    with pytest.raises(SpeechError) as caught:
        async with service.open(profile):
            pass

    assert caught.value.code == "MODEL_NOT_READY"
