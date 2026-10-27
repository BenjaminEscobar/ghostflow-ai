from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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
    meta_api_base_url: str = "https://api.llama.com/compat/v1"
    meta_model: str = "Llama-4-Maverick-17B-128E-Instruct-FP8"
    meta_timeout_s: float = 30.0

    browser_headless: bool = True
    step_timeout_ms: int = 10_000
    max_concurrent_runs: int = 2

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
