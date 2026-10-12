from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from order_vehicles.app import create_app
from order_vehicles.clients.python import cli
from order_vehicles.settings import Settings
from order_vehicles.speech.application.profiles import JsonRuntimeProfileRepository
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import (
    AudioFormat,
    AudioPacket,
    ManagementJob,
    RuntimeProfile,
    VoiceInfo,
    VoiceRef,
)


class FakeModels:
    async def list_models(self):
        return []


class FakeJobs:
    def __init__(self, error=None):
        self.job = ManagementJob(
            id=uuid4(), repo_id="known/model", status="running",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)
        )
        self.error = error

    async def start(self, repo_id):
        if self.error:
            raise self.error
        return self.job.model_copy(update={"repo_id": repo_id})

    def get(self, job_id):
        return self.job

    async def cancel(self, job_id):
        self.job = self.job.model_copy(update={"status": "cancel_requested"})
        return self.job

    async def close(self):
        pass


class FakeVoices:
    def __init__(self):
        self.filter = None

    async def list_voices(self, filter):
        self.filter = filter
        return [VoiceInfo(reference=VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234"), name="Nam", language="vi")]

    async def create_clone(self, reference, metadata):
        assert reference == b"RIFFaudio"
        return VoiceInfo(reference=VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234"), name=metadata.name)

    async def preview_voice(self, ref, text, config):
        yield AudioPacket(format=AudioFormat(sample_rate=48000), data=b"\x00\x00" * 8)

    async def delete_voice(self, ref):
        return None

    async def update_voice(self, ref, patch):
        return VoiceInfo(reference=ref, name=patch.get("name", "Nam"))

    async def use_archetype(self, archetype_id):
        return VoiceInfo(reference=VoiceRef(provider_id="voicestudio", kind="design", provider_voice_id="design123"), name=archetype_id)


class FakeManagement:
    def __init__(self, repository, delete_error=None):
        self.repository = repository
        self.delete_error = delete_error

    async def update_profile(self, profile, expected_revision):
        return self.repository.save(profile, expected_revision)

    async def delete_voice(self, ref):
        if self.delete_error:
            raise self.delete_error


def app_client(tmp_path, *, token="", jobs=None, delete_error=None):
    settings = Settings(
        state_dir=tmp_path / "state", output_dir=tmp_path / "out", management_token=token
    )
    repository = JsonRuntimeProfileRepository(tmp_path / "state" / "profile.json")
    voices = FakeVoices()
    app = create_app(settings, {
        "models": FakeModels(), "jobs": jobs or FakeJobs(), "voices": voices,
        "repository": repository, "management": FakeManagement(repository, delete_error),
    })
    return TestClient(app), voices


def test_management_mutation_auth_and_job_status(tmp_path) -> None:
    client, _ = app_client(tmp_path, token="admin")
    assert client.post("/v1/speech/models/install", json={"repo_id": "known/model"}).status_code == 401

    install = client.post("/v1/speech/models/install", json={"repo_id": "known/model"}, headers={"Authorization": "Bearer admin"})
    status = client.get(f"/v1/speech/models/jobs/{install.json()['id']}")
    cancelled = client.post(f"/v1/speech/models/jobs/{install.json()['id']}/cancel", headers={"Authorization": "Bearer admin"})

    assert install.status_code == 202
    assert status.status_code == 200
    assert cancelled.json()["status"] == "cancel_requested"


def test_upload_clone_filter_and_preview(tmp_path) -> None:
    client, voices = app_client(tmp_path)

    clone = client.post("/v1/speech/voices/clone", data={"name": "Nam", "ref_text": "xin chào", "language": "vi"}, files={"ref_audio": ("nam.wav", b"RIFFaudio", "audio/wav")})
    listed = client.get("/v1/speech/voices", params={"language": "vi", "kind": "clone"})
    ref = clone.json()["reference"]
    encoded = VoiceRef.model_validate(ref).encode()
    preview = client.post(f"/v1/speech/voices/{encoded}/preview", json={"text": "Nội dung mới"})

    assert clone.status_code == 201
    assert listed.status_code == 200 and voices.filter.language == "vi"
    assert preview.status_code == 200
    assert preview.headers["x-sample-rate"] == "48000"
    assert preview.content.startswith(b"RIFF")


def test_profile_revision_and_resource_in_use(tmp_path) -> None:
    error = SpeechError("RESOURCE_IN_USE", "voice busy")
    client, _ = app_client(tmp_path, delete_error=error)
    profile = client.get("/v1/speech/profile").json()

    saved = client.put("/v1/speech/profile", json={"profile": profile, "expected_revision": 1})
    stale = client.put("/v1/speech/profile", json={"profile": profile, "expected_revision": 1})
    ref = VoiceRef(provider_id="voicestudio", kind="clone", provider_voice_id="abcd1234").encode()
    deleted = client.delete(f"/v1/speech/voices/{ref}")

    assert saved.status_code == 200
    assert stale.status_code == 409
    assert deleted.status_code == 409


def test_local_only_error_does_not_leak_configured_secret(tmp_path) -> None:
    secret = "admin-secret"
    jobs = FakeJobs(SpeechError("UPSTREAM_FORBIDDEN", f"local-only ({secret})"))
    client, _ = app_client(tmp_path, token=secret, jobs=jobs)

    response = client.post("/v1/speech/models/install", json={"repo_id": "known/model"}, headers={"Authorization": f"Bearer {secret}"})

    assert response.status_code == 403
    assert secret not in response.text
    assert "local-only" in response.text


def test_non_loopback_requires_management_token(tmp_path) -> None:
    with pytest.raises(ValueError):
        create_app(Settings(host="0.0.0.0", state_dir=tmp_path / "s", output_dir=tmp_path / "o"))


def test_cli_models_list_exit_code(monkeypatch) -> None:
    class FakeClient:
        def get(self, path, params=None):
            return []
        def close(self):
            pass

    monkeypatch.setattr(cli, "create_management_client", lambda *_args, **_kwargs: FakeClient())

    result = CliRunner().invoke(cli.app, ["models", "list"])

    assert result.exit_code == 0


def test_cli_voice_preview_writes_audio(monkeypatch, tmp_path) -> None:
    class FakeClient:
        def post_bytes(self, path, **kwargs):
            return b"RIFFpreview"
        def close(self):
            pass

    monkeypatch.setattr(cli, "create_management_client", lambda *_args, **_kwargs: FakeClient())
    output = tmp_path / "preview.wav"

    result = CliRunner().invoke(cli.app, ["voices", "preview", "ref", "Xin chào", "--output", str(output)])

    assert result.exit_code == 0
    assert output.read_bytes() == b"RIFFpreview"
