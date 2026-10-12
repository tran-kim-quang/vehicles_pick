from datetime import datetime
from itertools import count
from uuid import UUID

import pytest

from order_vehicles.booking.models import CallSession


@pytest.fixture
def now() -> datetime:
    return datetime.fromisoformat("2026-10-12T23:30:00+07:00")


@pytest.fixture
def uuid_factory():
    values = count(1)
    return lambda: UUID(int=next(values))


@pytest.fixture
def session(uuid_factory) -> CallSession:
    return CallSession.create(["bike", "car"], uuid_factory=uuid_factory)
