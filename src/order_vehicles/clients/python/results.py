import json
import os
import tempfile
from pathlib import Path
from uuid import UUID


class ResultConflict(RuntimeError):
    pass


class ResultWriteError(RuntimeError):
    pass


class AtomicBookingWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.pending: dict[str, dict] = {}

    def write(self, payload: dict) -> Path:
        request_id = self._validate(payload)
        path = self.output_dir / f"{request_id}.json"
        encoded = self._encode(payload)
        if path.exists():
            if path.read_bytes() == encoded:
                self.pending.pop(str(request_id), None)
                return path
            raise ResultConflict(f"A different result already exists for {request_id}")
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, encoded)
        except FileExistsError:
            if path.read_bytes() == encoded:
                return path
            raise ResultConflict(f"A different result already exists for {request_id}")
        except OSError as exc:
            self.pending[str(request_id)] = payload
            raise ResultWriteError(f"Could not persist booking {request_id}: {exc}") from exc
        self.pending.pop(str(request_id), None)
        return path

    def retry(self, request_id: str) -> Path:
        key = str(UUID(str(request_id)))
        try:
            payload = self.pending[key]
        except KeyError as exc:
            raise KeyError(f"No pending result for {key}") from exc
        return self.write(payload)

    @staticmethod
    def _encode(payload: dict) -> bytes:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                separators=(",", ": "),
            )
            + "\n"
        ).encode()

    @staticmethod
    def _validate(payload: dict) -> UUID:
        request_id = UUID(str(payload.get("request_id", "")))
        if payload.get("status") != "confirmed":
            raise ValueError("Only confirmed bookings may be written")
        if payload.get("revision") != payload.get("confirmed_revision"):
            raise ValueError("Booking revision is not the confirmed revision")
        fields = payload.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("Confirmed booking fields are required")
        for value in fields.values():
            if not isinstance(value, dict) or "value" not in value:
                raise ValueError("Each field requires an id and value")
            UUID(str(value.get("id", "")))
        return request_id

    @staticmethod
    def _atomic_write(path: Path, encoded: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
