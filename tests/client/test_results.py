import json
from uuid import UUID

import pytest

from order_vehicles.clients.python.results import (
    AtomicBookingWriter,
    ResultConflict,
    ResultWriteError,
)


def payload(request_id="00000000-0000-0000-0000-000000000010"):
    return {
        "schema_version": "1.0",
        "request_id": request_id,
        "status": "confirmed",
        "revision": 4,
        "confirmed_revision": 4,
        "timezone": "Asia/Ho_Chi_Minh",
        "fields": {
            "name": {
                "id": "00000000-0000-0000-0000-000000000011",
                "value": "Nam",
            }
        },
    }


def test_duplicate_confirmed_event_keeps_same_file(tmp_path) -> None:
    writer = AtomicBookingWriter(tmp_path)
    first = writer.write(payload())
    second = writer.write(payload())
    assert first == second
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_existing_different_payload_is_not_overwritten(tmp_path) -> None:
    writer = AtomicBookingWriter(tmp_path)
    path = writer.write(payload())
    changed = payload()
    changed["fields"]["name"]["value"] = "An"
    with pytest.raises(ResultConflict):
        writer.write(changed)
    assert json.loads(path.read_text())["fields"]["name"]["value"] == "Nam"


def test_write_failure_is_visible_and_retryable(tmp_path, monkeypatch) -> None:
    writer = AtomicBookingWriter(tmp_path)
    original = writer._atomic_write
    monkeypatch.setattr(writer, "_atomic_write", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ResultWriteError):
        writer.write(payload())
    assert writer.pending
    monkeypatch.setattr(writer, "_atomic_write", original)
    path = writer.retry(payload()["request_id"])
    assert path.exists() and writer.pending == {}


def test_request_uuid_controls_safe_filename(tmp_path) -> None:
    writer = AtomicBookingWriter(tmp_path)
    request_id = "00000000-0000-0000-0000-000000000010"
    assert writer.write(payload(request_id)).name == f"{UUID(request_id)}.json"
    with pytest.raises(ValueError):
        writer.write(payload("../../escape"))


def test_unconfirmed_payload_is_rejected_without_file(tmp_path) -> None:
    writer = AtomicBookingWriter(tmp_path)
    candidate = payload()
    candidate["status"] = "draft"
    with pytest.raises(ValueError):
        writer.write(candidate)
    assert list(tmp_path.glob("*.json")) == []
