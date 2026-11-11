"""Runtime settings, read from the environment (and a local `.env` when present).

API keys are held as `SecretStr` so they never show up in logs, reprs or error pages.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/scribe/config.py -> repository root (only meaningful in a source checkout).
REPO_ROOT = Path(__file__).resolve().parents[3]

Effort = Literal["low", "medium", "high", "xhigh", "max"]
TranscriberName = Literal["auto", "gemini", "reference", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://scribe:scribe@localhost:5434/scribe"
    data_dir: Path = Path("data")
    samples_dir: Path = REPO_ROOT / "samples"
    seed_samples: bool = True

    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    soap_model: str = "claude-opus-5-5"
    soap_effort: Effort = "medium"
    check_model: str = "claude-opus-5-5"
    check_effort: Effort = "medium"
    # Ask the API to re-run a refused request on a fallback model chosen by refusal category.
    llm_fallbacks: bool = True

    transcriber: TranscriberName = "auto"
    # Chosen by eval/compare_transcribers.py: better speaker labels, much slower.
    gemini_transcribe_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = Field(default=600, gt=0)

    spacy_model: str = "en_core_web_md"

    retention_days: int = Field(default=30, ge=1)
    retention_interval_minutes: int = Field(default=60, ge=1)
    retention_schedule_enabled: bool = True

    max_upload_mb: int = Field(default=50, ge=1)
    cors_origins: list[str] = ["http://localhost:3400"]

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())

    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
