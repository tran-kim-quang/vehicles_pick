from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from order_vehicles.settings import Settings
from order_vehicles.speech.api import models as model_routes
from order_vehicles.speech.api import profiles as profile_routes
from order_vehicles.speech.api import voices as voice_routes
from order_vehicles.speech.application.jobs import ModelJobService
from order_vehicles.speech.application.leases import ResourceLeases
from order_vehicles.speech.application.management import SpeechManagementService
from order_vehicles.speech.application.profiles import JsonRuntimeProfileRepository
from order_vehicles.speech.domain.errors import SpeechError
from order_vehicles.speech.providers.voicestudio.client import VoiceStudioClient
from order_vehicles.speech.providers.voicestudio.models import VoiceStudioModelManager
from order_vehicles.speech.providers.voicestudio.tts import VoiceStudioTTSAdapter
from order_vehicles.speech.providers.voicestudio.voices import VoiceStudioVoiceLibrary


def create_app(
    settings: Settings | None = None,
    provider_overrides: dict[str, object] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.management_token.get_secret_value():
        raise ValueError("A management token is required on a non-loopback host")
    services, owned_client = _services(settings, provider_overrides or {})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        jobs = services.get("jobs")
        if jobs and hasattr(jobs, "close"):
            await jobs.close()
        if owned_client:
            await owned_client.aclose()

    app = FastAPI(title="Order Vehicles Voice API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.services = services
    app.include_router(model_routes.router)
    app.include_router(model_routes.contract_router)
    app.include_router(voice_routes.router)
    app.include_router(profile_routes.router)
    app.include_router(profile_routes.contract_router)

    @app.exception_handler(SpeechError)
    async def speech_error_handler(request: Request, exc: SpeechError):
        status = {
            "RESOURCE_IN_USE": 409,
            "REVISION_CONFLICT": 409,
            "UPSTREAM_CONFLICT": 409,
            "MODEL_MISMATCH": 409,
            "UPSTREAM_FORBIDDEN": 403,
            "UPSTREAM_UNAUTHORIZED": 401,
            "UPSTREAM_UNAVAILABLE": 503,
            "UPSTREAM_TIMEOUT": 503,
            "CLONE_UNSUPPORTED": 422,
            "CAPABILITY_UNVERIFIED": 422,
            "UNKNOWN_MODEL": 422,
        }.get(exc.code, 400)
        message = exc.message
        for secret in (
            settings.voicestudio_api_key.get_secret_value(),
            settings.management_token.get_secret_value(),
        ):
            if secret:
                message = message.replace(secret, "[redacted]")
        return JSONResponse(status_code=status, content={"error": {"code": exc.code, "message": message, "retryable": exc.retryable}})

    return app


def _services(settings: Settings, overrides: dict[str, object]):
    if overrides:
        return overrides, None
    client = VoiceStudioClient(settings)
    models = VoiceStudioModelManager(client)
    tts = VoiceStudioTTSAdapter(client)

    async def clone_capability(_: str):
        engines = await models.list_engines()
        matching = [engine for engine in engines if engine.family == "tts" and engine.selected]
        return matching[0].capabilities.get("voice_clone") if matching else None

    voices = VoiceStudioVoiceLibrary(client, tts, clone_capability=clone_capability)
    repository = JsonRuntimeProfileRepository(settings.state_dir / "speech-profile.json")
    leases = ResourceLeases()
    jobs = ModelJobService(models, tracking_timeout_seconds=settings.install_tracking_timeout_seconds)
    management = SpeechManagementService(repository, leases, models, voices)
    return {
        "models": models,
        "jobs": jobs,
        "voices": voices,
        "repository": repository,
        "leases": leases,
        "management": management,
    }, client


app = create_app()
