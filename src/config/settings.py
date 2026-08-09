"""Environment settings without allowing model selection from the environment."""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from src.errors import ConfigurationError


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    openrouter_base_url: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_read_user: str
    postgres_read_password: str
    app_env: str
    log_level: str
    max_concurrent_cases: int
    max_concurrent_llm_calls: int
    llm_request_timeout_seconds: float
    llm_max_retries: int

    @property
    def admin_database_url(self) -> str:
        return self._database_url(self.postgres_user, self.postgres_password)

    @property
    def read_database_url(self) -> str:
        return self._database_url(self.postgres_read_user, self.postgres_read_password)

    def _database_url(self, user: str, password: str) -> str:
        return (
            f"postgresql+psycopg://{user}:{password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def require_api_key(self) -> None:
        if not self.openrouter_api_key.strip():
            raise ConfigurationError("OPENROUTER_API_KEY is empty")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(override=False)
    return Settings(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "olist"),
        postgres_user=os.getenv("POSTGRES_USER", "olist"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "olist_password"),
        postgres_read_user=os.getenv("POSTGRES_READ_USER", "olist_reader"),
        postgres_read_password=os.getenv("POSTGRES_READ_PASSWORD", "olist_reader_password"),
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_concurrent_cases=int(os.getenv("MAX_CONCURRENT_CASES", "4")),
        max_concurrent_llm_calls=int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "4")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
        llm_request_timeout_seconds=float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45")),
    )
