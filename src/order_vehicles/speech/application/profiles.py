import os
import tempfile
import threading
from pathlib import Path

from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import RuntimeProfile


class JsonRuntimeProfileRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def get(self) -> RuntimeProfile:
        with self._lock:
            if not self.path.exists():
                return RuntimeProfile.default()
            return RuntimeProfile.model_validate_json(self.path.read_bytes())

    def save(
        self, profile: RuntimeProfile, expected_revision: int
    ) -> RuntimeProfile:
        with self._lock:
            current = self.get()
            if current.revision != expected_revision:
                raise SpeechError(
                    "REVISION_CONFLICT",
                    f"Expected profile revision {expected_revision}, found {current.revision}",
                )
            saved = profile.model_copy(update={"revision": current.revision + 1})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(saved.model_dump_json(indent=2).encode("utf-8"))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
            return saved

    def assert_revision(self, expected_revision: int) -> RuntimeProfile:
        current = self.get()
        if current.revision != expected_revision:
            raise SpeechError(
                "REVISION_CONFLICT",
                f"Expected profile revision {expected_revision}, found {current.revision}",
            )
        return current
