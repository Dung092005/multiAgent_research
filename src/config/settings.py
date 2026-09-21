"""Environment settings without allowing model selection from the environment."""

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import load_dotenv

from src.errors import ConfigurationError


def _as_async_postgres_url(url: str) -> str:
    value = url.strip().strip('"').strip("'")
    if value.startswith("postgresql+psycopg://"):
        return value
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


def _parts_from_database_url(url: str) -> dict[str, str | int]:
    parsed = urlparse(url.strip().strip('"').strip("'"))
    return {
        "host": parsed.hostname or "",
        "port": int(parsed.port or 5432),
        "db": (parsed.path or "/").lstrip("/") or "postgres",
        "user": parsed.username or "",
        "password": parsed.password or "",
    }


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    openrouter_api_key: str
    openrouter_base_url: str
    google_cloud_project: str
    vertex_location: str
    google_api_key: str
    database_url: str
    database_url_unpooled: str
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
        # Direct (non-pooled) for writes / migrations when available.
        if self.database_url_unpooled.strip():
            return _as_async_postgres_url(self.database_url_unpooled)
        if self.database_url.strip():
            return _as_async_postgres_url(self.database_url)
        return self._database_url(self.postgres_user, self.postgres_password)

    @property
    def read_database_url(self) -> str:
        # Pooled URL preferred for app reads.
        if self.database_url.strip():
            return _as_async_postgres_url(self.database_url)
        if self.database_url_unpooled.strip():
            return _as_async_postgres_url(self.database_url_unpooled)
        return self._database_url(self.postgres_read_user, self.postgres_read_password)

    def _database_url(self, user: str, password: str) -> str:
        return (
            f"postgresql+psycopg://{user}:{password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            "?sslmode=require"
        )

    @property
    def vertex_openai_base_url(self) -> str:
        location = self.vertex_location.strip() or "us-central1"
        project = self.google_cloud_project.strip()
        if location == "global":
            return (
                f"https://aiplatform.googleapis.com/v1/projects/{project}"
                "/locations/global/endpoints/openapi"
            )
        return (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{location}/endpoints/openapi"
        )

    def require_api_key(self) -> None:
        provider = self.llm_provider.strip().lower() or "vertex"
        if provider == "vertex":
            if not self.google_cloud_project.strip():
                raise ConfigurationError("GOOGLE_CLOUD_PROJECT is empty")
            return
        if provider == "openrouter" and not self.openrouter_api_key.strip():
            raise ConfigurationError("OPENROUTER_API_KEY is empty")
        if provider == "google_ai" and not self.google_api_key.strip():
            raise ConfigurationError("GOOGLE_API_KEY is empty")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Project .env wins over shell defaults (e.g. a global LLM_PROVIDER=openai).
    load_dotenv(override=True)

    database_url = os.getenv("DATABASE_URL", "")
    database_url_unpooled = os.getenv("DATABASE_URL_UNPOOLED", "")
    neon_parts = _parts_from_database_url(database_url or database_url_unpooled) if (
        database_url or database_url_unpooled
    ) else None

    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "vertex"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        google_cloud_project=os.getenv(
            "GOOGLE_CLOUD_PROJECT",
            os.getenv("VERTEX_PROJECT_ID", "project-3b0c96e7-a43e-4f65-8bd"),
        ),
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        database_url=database_url,
        database_url_unpooled=database_url_unpooled,
        postgres_host=os.getenv(
            "POSTGRES_HOST",
            str(neon_parts["host"]) if neon_parts else "postgres",
        ),
        postgres_port=int(
            os.getenv(
                "POSTGRES_PORT",
                str(neon_parts["port"]) if neon_parts else "5432",
            )
        ),
        postgres_db=os.getenv(
            "POSTGRES_DB",
            str(neon_parts["db"]) if neon_parts else "olist",
        ),
        postgres_user=os.getenv(
            "POSTGRES_USER",
            str(neon_parts["user"]) if neon_parts else "olist",
        ),
        postgres_password=os.getenv(
            "POSTGRES_PASSWORD",
            str(neon_parts["password"]) if neon_parts else "olist_password",
        ),
        postgres_read_user=os.getenv(
            "POSTGRES_READ_USER",
            str(neon_parts["user"]) if neon_parts else "olist_reader",
        ),
        postgres_read_password=os.getenv(
            "POSTGRES_READ_PASSWORD",
            str(neon_parts["password"]) if neon_parts else "olist_reader_password",
        ),
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_concurrent_cases=int(os.getenv("MAX_CONCURRENT_CASES", "4")),
        max_concurrent_llm_calls=int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "4")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
        llm_request_timeout_seconds=float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45")),
    )
