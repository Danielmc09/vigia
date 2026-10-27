"""Plain-SQL migration runner: applies store/migrations/*.sql once, in order (D-021)."""

import asyncio
from pathlib import Path

import asyncpg

from vigia.config import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_LOCK_KEY = 7_419_001  # serializes concurrent runners (replicas, tests)


async def apply_migrations(dsn: str) -> list[str]:
    """Apply pending migrations, each in its own transaction.

    Args:
        dsn: DSN of the schema owner role.

    Returns:
        Versions (file stems) applied by this call; empty if the schema was current.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        done = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in applied:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", path.stem)
            done.append(path.stem)
        return done
    finally:
        await conn.close()


if __name__ == "__main__":
    print(asyncio.run(apply_migrations(settings.migration_database_url)))
