from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from GHOSTFLOW_* environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_prefix="GHOSTFLOW_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./ghostflow.db"

    # Comma-separated list of origins (scheme://host[:port]) that may be recorded and automated.
    # Anything else is rejected at ingestion, at workflow validation and at the browser network layer.
    allowed_origins: str = "http://localhost:8080,http://127.0.0.1:8080"

    # AI provider. "mock" never leaves the process; "meta" calls the Meta Model API (opt-in).
    ai_provider: Literal["mock", "meta"] = "mock"
    meta_api_key: SecretStr | None = None
    meta_api_base_url: str = "https://api.meta.ai/v1"
    meta_model: str = "muse-spark-1.3"
    meta_timeout_s: float = Field(default=60.0, gt=0, le=300)
    # Caps reasoning + visible output tokens of one call (`max_completion_tokens`).
    meta_max_output_tokens: int = Field(default=4000, ge=256, le=32_000)
    meta_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    # Retries for 429/500/503 only; every attempt counts against meta_max_requests.
    meta_max_retries: int = Field(default=1, ge=0, le=3)
    # Hard cap of HTTP requests to Meta per backend process (no unbounded spend).
    meta_max_requests: int = Field(default=50, ge=1, le=10_000)
    # "-contributor" models let Meta train on the data sent. Off unless explicitly authorised.
    meta_allow_contributor_mode: bool = False

    browser_headless: bool = True
    step_timeout_ms: int = 10_000
    max_concurrent_runs: int = 2

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
