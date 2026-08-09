"""Async SQLAlchemy engine and session creation."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.settings import Settings, get_settings


def create_engine(
    settings: Settings | None = None,
    *,
    read_only: bool = True,
    schema_translate_map: dict[str, str] | None = None,
) -> AsyncEngine:
    configured = settings or get_settings()
    url = configured.read_database_url if read_only else configured.admin_database_url
    options: dict[str, object] = {"pool_pre_ping": True}
    if schema_translate_map:
        options["execution_options"] = {"schema_translate_map": schema_translate_map}
    return create_async_engine(url, **options)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
