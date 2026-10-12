import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from order_vehicles.observability.timing import TimingRecorder
from order_vehicles.settings import Settings

from .management import ManagementClient
from .microphone import PythonMicClient
from .recording import record_reference
from .results import AtomicBookingWriter
from .turns import TurnController
from .vad import EnergyVAD

app = typer.Typer(help="Voice booking and speech management CLI")
models_app = typer.Typer(help="Manage VoiceStudio models")
voices_app = typer.Typer(help="Manage voices")
profile_app = typer.Typer(help="Manage the runtime speech profile")
app.add_typer(models_app, name="models")
app.add_typer(voices_app, name="voices")
app.add_typer(profile_app, name="profile")


def create_management_client(url: str, token: str) -> ManagementClient:
    return ManagementClient(url, token)


def _client(url: str, token: str):
    return create_management_client(url, token)


@app.command()
def server() -> None:
    import uvicorn
    settings = Settings()
    uvicorn.run("order_vehicles.app:app", host=settings.host, port=settings.port)


@app.command()
def devices() -> None:
    """List host audio devices."""
    try:
        import sounddevice
    except ImportError as exc:
        raise typer.BadParameter(
            "Audio support requires: uv sync --extra audio"
        ) from exc
    values = [dict(item) for item in sounddevice.query_devices()]
    typer.echo(json.dumps(values, ensure_ascii=False, indent=2, default=str))


@app.command()
def mic(
    url: str = "ws://127.0.0.1:8000/v1/voice/sessions",
    input_device: int | None = None,
    output_device: int | None = None,
    push_to_talk: bool = False,
    vad_threshold: float | None = None,
) -> None:
    """Run the microphone booking client."""
    settings = Settings()
    timings = TimingRecorder()
    threshold = settings.vad_threshold if vad_threshold is None else vad_threshold
    client = PythonMicClient(
        writer=AtomicBookingWriter(settings.output_dir),
        turn_controller=TurnController(
            EnergyVAD(threshold),
            max_turn_ms=round(settings.max_turn_seconds * 1000),
        ),
        timing_recorder=timings,
    )
    try:
        asyncio.run(
            client.run(url, input_device, output_device, push_to_talk)
        )
    except KeyboardInterrupt:
        typer.echo("Đã dừng.")
    finally:
        summary = timings.summary()
        if summary:
            typer.echo(json.dumps({"timings": summary}, ensure_ascii=False, indent=2))


@models_app.command("list")
def models_list(url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.get("/v1/speech/models"), ensure_ascii=False, indent=2))
    finally:
        client.close()


@models_app.command("install")
def models_install(repo_id: str, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.post("/v1/speech/models/install", json={"repo_id": repo_id}), indent=2))
    finally:
        client.close()


@models_app.command("status")
def models_status(job_id: str, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.get(f"/v1/speech/models/jobs/{job_id}"), indent=2))
    finally:
        client.close()


@models_app.command("cancel")
def models_cancel(job_id: str, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.post(f"/v1/speech/models/jobs/{job_id}/cancel"), indent=2))
    finally:
        client.close()


@voices_app.command("list")
def voices_list(language: str = "", kind: str = "", url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.get("/v1/speech/voices", {"language": language or None, "kind": kind or None}), ensure_ascii=False, indent=2))
    finally:
        client.close()


@voices_app.command("clone")
def voices_clone(audio: Path, name: str, ref_text: str = "", language: str = "vi", url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        with audio.open("rb") as source:
            result = client.post("/v1/speech/voices/clone", data={"name": name, "ref_text": ref_text, "language": language}, files={"ref_audio": (audio.name, source)})
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


@voices_app.command("record")
def voices_record(
    name: Annotated[str, typer.Option("--name")],
    seconds: Annotated[float, typer.Option("--seconds")] = 8.0,
    device: int | None = None,
    ref_text: str = "",
    language: str = "vi",
    url: str = "http://127.0.0.1:8000",
    token: str = "",
) -> None:
    """Record a standalone reference WAV and create a voice clone."""
    wav = asyncio.run(record_reference(seconds, device))
    client = _client(url, token)
    try:
        result = client.post(
            "/v1/speech/voices/clone",
            data={"name": name, "ref_text": ref_text, "language": language},
            files={"ref_audio": ("reference.wav", wav, "audio/wav")},
        )
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


@voices_app.command("preview")
def voices_preview(ref: str, text: str, output: Path = Path("preview.wav"), url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        output.write_bytes(client.post_bytes(f"/v1/speech/voices/{ref}/preview", json={"text": text}))
        typer.echo(str(output))
    finally:
        client.close()


@voices_app.command("from-archetype")
def voices_from_archetype(archetype_id: str, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.post(f"/v1/speech/voices/archetypes/{archetype_id}/use"), ensure_ascii=False))
    finally:
        client.close()


@voices_app.command("update")
def voices_update(ref: str, name: str, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.client.patch(f"/v1/speech/voices/{ref}", json={"name": name}).json(), ensure_ascii=False))
    finally:
        client.close()


@voices_app.command("delete")
def voices_delete(ref: str, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        client.delete(f"/v1/speech/voices/{ref}")
    finally:
        client.close()


@profile_app.command("show")
def profile_show(url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        typer.echo(json.dumps(client.get("/v1/speech/profile"), ensure_ascii=False, indent=2))
    finally:
        client.close()


@profile_app.command("set")
def profile_set(file: Path, expected_revision: int, url: str = "http://127.0.0.1:8000", token: str = "") -> None:
    client = _client(url, token)
    try:
        payload = json.loads(file.read_text())
        typer.echo(json.dumps(client.put("/v1/speech/profile", json={"profile": payload, "expected_revision": expected_revision}), ensure_ascii=False, indent=2))
    finally:
        client.close()


def main() -> None:
    app()
