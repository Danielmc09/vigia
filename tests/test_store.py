import re

import asyncpg
import pytest

from vigia.store.migrate import apply_migrations

pytestmark = pytest.mark.anyio


async def test_migrations_are_idempotent_and_recorded(admin_dsn: str) -> None:
    await apply_migrations(admin_dsn)

    assert await apply_migrations(admin_dsn) == []
    conn = await asyncpg.connect(admin_dsn)
    try:
        versions = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
    finally:
        await conn.close()
    assert "0001_init" in versions


async def test_app_role_cannot_bypass_row_level_security(app_conn: asyncpg.Connection) -> None:
    role = await app_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )

    assert role is not None
    assert role["rolsuper"] is False
    assert role["rolbypassrls"] is False


async def test_security_definer_functions_pin_search_path_and_qualify_tables(
    admin_conn: asyncpg.Connection,
) -> None:
    rows = await admin_conn.fetch(
        "SELECT proname, prosecdef, proconfig, prosrc FROM pg_proc"
        " WHERE proname IN ('vigia_find_member', 'vigia_record_rejection')"
    )

    assert {r["proname"] for r in rows} == {"vigia_find_member", "vigia_record_rejection"}
    for row in rows:
        assert row["prosecdef"] is True
        assert "search_path=public, pg_temp" in (row["proconfig"] or [])
        tables = re.findall(r"(?:FROM|INTO)\s+([\w.]+)", row["prosrc"], re.I)
        assert tables and all(name.startswith("public.") for name in tables), tables
