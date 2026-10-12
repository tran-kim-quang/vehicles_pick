import threading
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from order_vehicles.speech.domain.errors import SpeechError

from .models import CallSession


class SessionRepository:
    def __init__(self, vehicles: list[str], *, uuid_factory=uuid4, ttl_seconds: int = 900) -> None:
        self.vehicles = vehicles
        self.uuid_factory = uuid_factory
        self.ttl = timedelta(seconds=ttl_seconds)
        self._sessions: dict[UUID, CallSession] = {}
        self._lock = threading.RLock()

    def create(self, profile_ref: str) -> CallSession:
        with self._lock:
            session = CallSession.create(self.vehicles, uuid_factory=self.uuid_factory, profile_ref=profile_ref)
            self._sessions[session.session_id] = session.model_copy(deep=True)
            return session

    def get(self, session_id: UUID | str) -> CallSession:
        session_id = UUID(str(session_id))
        with self._lock:
            try:
                session = self._sessions[session_id].model_copy(deep=True)
            except KeyError as exc:
                raise SpeechError("SESSION_NOT_FOUND", "Session not found") from exc
            if session.detached_at:
                session.pending_confirmation_revision = None
                session.confirmation_response_id = None
                session.playback_finished = False
            return session

    def save(self, session: CallSession, expected_revision: int) -> None:
        with self._lock:
            stored = self._sessions.get(session.session_id)
            if stored is None:
                raise SpeechError("SESSION_NOT_FOUND", "Session not found")
            if stored.revision != expected_revision:
                raise SpeechError("REVISION_CONFLICT", "Session revision changed")
            self._sessions[session.session_id] = session.model_copy(deep=True)

    def detach(self, session_id: UUID | str, now: datetime) -> None:
        session_id = UUID(str(session_id))
        with self._lock:
            session = self._sessions[session_id]
            session.detached_at = now
            session.pending_confirmation_revision = None
            session.confirmation_response_id = None
            session.playback_finished = False

    def expire(self, now: datetime) -> None:
        with self._lock:
            expired = [key for key, value in self._sessions.items() if value.detached_at and now - value.detached_at > self.ttl]
            for key in expired:
                del self._sessions[key]
