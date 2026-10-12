from pathlib import Path

import pytest

from order_vehicles.speech.application.profiles import JsonRuntimeProfileRepository
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import RuntimeProfile


def test_concurrent_profile_update_uses_revision(tmp_path: Path) -> None:
    repository = JsonRuntimeProfileRepository(tmp_path / "profile.json")
    original = repository.get()
    repository.save(original.model_copy(update={"language": "en"}), expected_revision=1)

    with pytest.raises(SpeechError) as caught:
        repository.save(original.model_copy(update={"language": "vi"}), expected_revision=1)

    assert caught.value.code == "REVISION_CONFLICT"


def test_profile_survives_repository_recreation(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    first = JsonRuntimeProfileRepository(path)
    saved = first.save(
        RuntimeProfile.default().model_copy(update={"language": "en"}),
        expected_revision=1,
    )

    second = JsonRuntimeProfileRepository(path)

    assert saved.revision == 2
    assert second.get().language == "en"
    assert second.get().revision == 2
