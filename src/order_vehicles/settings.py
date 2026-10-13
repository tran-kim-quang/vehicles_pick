from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
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
    service_timezone: str = "Asia/Ho_Chi_Minh"
    vehicles_available: list[str] = Field(default_factory=lambda: ["bike", "car"])
    vad_threshold: float = 500
    install_tracking_timeout_seconds: float = 1800
    shutdown_timeout_seconds: float = 5
    default_tts_engine: str = "omnivoice"
    default_language: str = "vi"
    exact_model: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    llm_structured_output: bool = False
    llm_timeout_seconds: float = 30
    tts_provider: str = "voicestudio"
    vieneu_base_url: str = Field(default="https://api.vieneu.io/api/v1", validation_alias=AliasChoices("OV_VIENEU_BASE_URL", "BASE_URL_TTS"))
    vieneu_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), validation_alias=AliasChoices("OV_VIENEU_API_KEY", "TTS_API_KEY"))
    vieneu_model: str = "vieneu-v4"
    vieneu_voice: str = "Ngọc Lan"
    vieneu_timeout_seconds: float = 120
    greeting_audio_path: Path = Path("assets/greeting.wav")

    @field_validator("tts_provider")
    @classmethod
    def validate_tts_provider(cls, value: str) -> str:
        if value not in {"voicestudio", "vieneu"}:
            raise ValueError("tts_provider must be voicestudio or vieneu")
        return value

    @field_validator("vieneu_base_url")
    @classmethod
    def normalize_vieneu_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if value.endswith("/audio/speech"):
            value = value[:-len("/audio/speech")]
        if value.endswith("/api"):
            value += "/v1"
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("vieneu_base_url must be an HTTP API URL")
        return value

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
        for name in ("voicestudio_api_key", "management_token", "llm_api_key", "vieneu_api_key"):
            result[name] = "**********" if getattr(self, name).get_secret_value() else ""
        return result
