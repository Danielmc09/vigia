"""Shared fixtures: a real Postgres (docker compose) with migrations applied."""

from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import asyncpg
import pytest

from vigia.config import Settings
from vigia.store.migrate import apply_migrations


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _where(dsn: str) -> str:
    """Host:port/db of a DSN, without credentials, for error messages."""
    parts = urlsplit(dsn)
    return f"{parts.hostname}:{parts.port}{parts.path}"


@pytest.fixture
async def admin_dsn() -> str:
    dsn = Settings().migration_database_url
    try:
        await apply_migrations(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.fail(
            f"Postgres not reachable at {_where(dsn)} ({exc!r}). "
            "Start it with `make db` (docker compose up -d --wait postgres).",
            pytrace=False,
        )
    return dsn


@pytest.fixture
async def app_conn(admin_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """Connection as the application role, inside a transaction that is rolled back."""
    conn = await asyncpg.connect(Settings().database_url)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()
