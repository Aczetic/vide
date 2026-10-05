"""Application settings, loaded from the environment (.env in development)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://vide:vide@localhost:5433/vide"

    # provider layer. OpenRouter is the default LLM route; Higgsfield arrives later.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    higgsfield_api_key: str = ""
    #: Optional. When set, tiers may route Claude tasks natively instead of
    #: through OpenRouter, which buys schema-guaranteed output, prompt caching
    #: and batch. Without it every task falls back to OpenRouter.
    anthropic_api_key: str = ""

    # a later stage lists object storage, but nothing in extraction writes a binary.
    storage_endpoint: str = ""
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
