import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from order_vehicles.app import create_app
from order_vehicles.settings import Settings
from order_vehicles.voice.protocol import parse_client_event


def test_session_start_validates_audio_format() -> None:
    event = parse_client_event(
        {
            "type": "session.start",
            "version": "voice.booking.v1",
            "audio_format": {
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
                "channels": 1,
            },
        }
    )
    assert event.audio_format.sample_rate == 16000

    with pytest.raises(ValidationError):
        parse_client_event(
            {
                "type": "session.start",
                "version": "voice.booking.v1",
                "audio_format": {
                    "encoding": "pcm_s16le",
                    "sample_rate": 48000,
                    "channels": 1,
                },
            }
        )


def test_resume_requires_token() -> None:
    with pytest.raises(ValidationError):
        parse_client_event(
            {
                "type": "session.resume",
                "version": "voice.booking.v1",
                "session_id": "00000000-0000-0000-0000-000000000001",
            }
        )


def test_websocket_route_delegates_to_voice_service(tmp_path) -> None:
    class Service:
        async def run(self, socket):
            first = await socket.receive()
            await socket.send_json({"type": "echo", "value": first["type"]})

    app = create_app(
        Settings(state_dir=tmp_path / "state", output_dir=tmp_path / "output"),
        {"voice_sessions": Service()},
    )
    with TestClient(app).websocket_connect("/v1/voice/sessions") as socket:
        socket.send_json({"type": "hello"})
        assert socket.receive_json() == {"type": "echo", "value": "hello"}
