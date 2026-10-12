import os
from pathlib import Path

import pytest

from order_vehicles.clients.python.microphone import PythonMicClient
from order_vehicles.clients.python.results import AtomicBookingWriter


@pytest.mark.asyncio
async def test_live_micro_voice_booking() -> None:
    if os.getenv("RUN_LIVE_VOICE") != "1":
        pytest.skip("pending: set RUN_LIVE_VOICE=1 after preparing models, LLM, and a headset")
    required = ["OV_LLM_BASE_URL", "OV_LLM_MODEL", "VOICE_BOOKING_WS_URL"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.fail(f"Missing live voice prerequisites: {', '.join(missing)}")
    output = Path(os.environ.get("OV_OUTPUT_DIR", "outputs"))
    before = set(output.glob("*.json")) if output.exists() else set()
    client = PythonMicClient(writer=AtomicBookingWriter(output))
    await client.run(
        os.environ["VOICE_BOOKING_WS_URL"],
        int(os.environ["VOICE_INPUT_DEVICE"]) if os.getenv("VOICE_INPUT_DEVICE") else None,
        int(os.environ["VOICE_OUTPUT_DEVICE"]) if os.getenv("VOICE_OUTPUT_DEVICE") else None,
        os.getenv("VOICE_PUSH_TO_TALK") == "1",
    )
    created = set(output.glob("*.json")) - before
    assert len(created) == 1, "A confirmed live call must create exactly one new JSON result"
