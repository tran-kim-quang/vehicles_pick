import json
import wave
from io import BytesIO

import httpx
import pytest

from order_vehicles.settings import Settings
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.domain.models import (
    AudioFormat,
    AudioPacket,
    ModelSelection,
    SpeechConfig,
)
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.stt import VoiceStudioSTTAdapter
from tests.speech.fakes import ScriptedConnector, byte_frames


def config(*, exact: bool = False, engine: str = "capture") -> SpeechConfig:
    return SpeechConfig(
        stt=ModelSelection(engine_id=engine, model_id="vi-model", purpose="stt_stream"),
        tts=ModelSelection(engine_id="omnivoice", purpose="tts"),
        exact_model=exact,
    )


@pytest.mark.asyncio
async def test_batch_transcription_wraps_pcm_as_wav(tmp_path) -> None:
    captured = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request.content
        return httpx.Response(200, json={"text": "xin chào", "engine": "whisper"})

    client = VoiceStudioClient(
        Settings(state_dir=tmp_path / "state", output_dir=tmp_path / "out"),
        transport=httpx.MockTransport(handler),
    )
    adapter = VoiceStudioSTTAdapter(client)
    event = await adapter.transcribe(
        AudioPacket(format=AudioFormat(sample_rate=16000), data=b"\x01\x00" * 160),
        config(),
    )
    await client.aclose()

    body = captured.split(b"\r\n\r\n", 1)[1]
    wav_start = body.index(b"RIFF")
    wav_end = body.index(b"\r\n", wav_start)
    with wave.open(BytesIO(body[wav_start:wav_end]), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
    assert event.text == "xin chào"


@pytest.mark.asyncio
async def test_summary_is_authoritative(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "transcript.partial", "text": "Nam"},
        {"type": "transcript.final", "final_kind": "utterance", "text": "Nam", "refined_text": "Lan"},
        {"type": "transcript.final", "final_kind": "summary", "text": "Nam muốn đặt xe", "refined_text": "Lan"},
    ]])
    client = VoiceStudioClient(Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"), websocket_connector=connector)
    adapter = VoiceStudioSTTAdapter(client)

    events = [event async for event in adapter.stream_transcribe(byte_frames(b"\x00\x00"), config())]
    summary = [event for event in events if event.final_kind == "summary"][0]
    assert "language=vi" in connector.urls[0]
    assert "model=vi-model" in connector.urls[0]

    assert summary.text == "Nam muốn đặt xe"
    assert json.loads(connector.connections[0].sent[-1]) == {"type": "input_audio.end"}


@pytest.mark.asyncio
async def test_socket_closes_after_summary(tmp_path) -> None:
    connector = ScriptedConnector([[["unused"]]])
    connector = ScriptedConnector([[{"type": "transcript.final", "final_kind": "summary", "text": "x"}]])
    client = VoiceStudioClient(Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"), websocket_connector=connector)

    _ = [event async for event in VoiceStudioSTTAdapter(client).stream_transcribe(byte_frames(b"00"), config())]

    assert connector.open_connections == 0


@pytest.mark.asyncio
async def test_fallback_reports_actual_engine(tmp_path) -> None:
    connector = ScriptedConnector([[{"type": "transcript.final", "final_kind": "summary", "text": "x", "engine": "fallback"}]])
    client = VoiceStudioClient(Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"), websocket_connector=connector)

    events = [event async for event in VoiceStudioSTTAdapter(client).stream_transcribe(byte_frames(), config())]

    assert events[-1].actual_engine == "fallback"


@pytest.mark.asyncio
async def test_exact_model_rejects_fallback(tmp_path) -> None:
    connector = ScriptedConnector([[{"type": "transcript.final", "final_kind": "summary", "text": "x", "engine": "fallback"}]])
    client = VoiceStudioClient(Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"), websocket_connector=connector)

    with pytest.raises(SpeechError) as caught:
        _ = [event async for event in VoiceStudioSTTAdapter(client).stream_transcribe(byte_frames(), config(exact=True))]

    assert caught.value.code == "MODEL_MISMATCH"


@pytest.mark.asyncio
async def test_transcript_disconnect_does_not_commit_partial(tmp_path) -> None:
    connector = ScriptedConnector([[
        {"type": "transcript.partial", "text": "Nam"},
        ConnectionError("gone"),
    ]])
    client = VoiceStudioClient(Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"), websocket_connector=connector)
    summaries = []

    with pytest.raises(SpeechError):
        async for event in VoiceStudioSTTAdapter(client).stream_transcribe(byte_frames(), config()):
            if event.final_kind == "summary":
                summaries.append(event)

    assert summaries == []


@pytest.mark.asyncio
async def test_sherpa_model_query_is_explicit(tmp_path) -> None:
    connector = ScriptedConnector([[{"type": "transcript.final", "final_kind": "summary", "text": "x"}]])
    client = VoiceStudioClient(Settings(state_dir=tmp_path / "s", output_dir=tmp_path / "o"), websocket_connector=connector)

    _ = [event async for event in VoiceStudioSTTAdapter(client).stream_transcribe(byte_frames(), config(engine="sherpa-onnx"))]

    assert connector.urls == ["ws://127.0.0.1:3900/v1/audio/transcriptions/stream?pcm=1&sr=16000&language=vi&model=vi-model"]


@pytest.mark.asyncio
async def test_faster_whisper_route_pins_engine_model_and_vietnamese(tmp_path):
    connector = ScriptedConnector([[{"type": "final", "final_kind": "summary", "text": "xin chào", "engine": "faster-whisper"}]])
    client = VoiceStudioClient(Settings(), websocket_connector=connector)
    selected = config(engine="faster-whisper").model_copy(update={"stt": ModelSelection(engine_id="faster-whisper", model_id="Systran/faster-whisper-large-v3", purpose="stt_stream")})
    _ = [event async for event in VoiceStudioSTTAdapter(client).stream_transcribe(byte_frames(), selected)]
    assert "engine=faster-whisper" in connector.urls[0]
    assert "language=vi" in connector.urls[0]
