from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OV_", env_file=".env", extra="ignore"
    )

    voicestudio_url: str = "http://127.0.0.1:3900"
    voicestudio_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    management_token: SecretStr = Field(default_factory=lambda: SecretStr(""))
    state_dir: Path = Path("state")
    output_dir: Path = Path("outputs")
    connect_timeout_seconds: float = 10
    rest_timeout_seconds: float = 30
    inference_inactivity_timeout_seconds: float = 60
    max_turn_seconds: float = 30
    install_tracking_timeout_seconds: float = 1800
    shutdown_timeout_seconds: float = 5
    default_tts_engine: str = "omnivoice"
    default_language: str = "vi"
    exact_model: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    @field_validator("voicestudio_url")
    @classmethod
    def validate_service_root(cls, value: str) -> str:
        value = value.rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("voicestudio_url must be an HTTP service root")
        if parsed.path not in {"", "/"}:
            raise ValueError("voicestudio_url must not include /v1 or another path")
        return value

    def safe_dict(self) -> dict[str, Any]:
        result = self.model_dump(mode="json")
        for name in ("voicestudio_api_key", "management_token"):
            result[name] = "**********" if getattr(self, name).get_secret_value() else ""
        return result
