"""OWASP Web Top 10 checks that apply from T-00 (specs/10 section 5)."""

import ast
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient

from vigia.app import create_app
from vigia.config import Settings
from vigia.store.db import set_business

SRC = Path(__file__).parents[1] / "src" / "vigia"


# --- A01 Broken access control: one business never sees another's rows -------------


async def _new_business_with_case(conn: asyncpg.Connection) -> tuple:
    business_id, case_id = uuid4(), uuid4()
    await set_business(conn, business_id)
    await conn.execute(
        "INSERT INTO businesses (id, dek_enc, name_enc, country, language)"
        " VALUES ($1, 'k', 'n', 'CO', 'es')",
        business_id,
    )
    await conn.execute(
        "INSERT INTO cases (id, business_id, kind, origin, text_enc)"
        " VALUES ($1, $2, 'text', 'local', $3)",
        case_id,
        business_id,
        b"ciphertext",
    )
    return business_id, case_id


@pytest.mark.anyio
async def test_a01_business_sees_only_its_own_rows(app_conn: asyncpg.Connection) -> None:
    business_a, case_a = await _new_business_with_case(app_conn)
    business_b, _ = await _new_business_with_case(app_conn)

    await set_business(app_conn, business_a)
    cases = await app_conn.fetch("SELECT id, business_id FROM cases")
    businesses = await app_conn.fetch("SELECT id FROM businesses")

    assert [(r["id"], r["business_id"]) for r in cases] == [(case_a, business_a)]
    assert [r["id"] for r in businesses] == [business_a]
    assert business_b not in {r["id"] for r in businesses}


@pytest.mark.anyio
async def test_a01_without_business_context_no_rows_are_visible(
    app_conn: asyncpg.Connection,
) -> None:
    await _new_business_with_case(app_conn)
    await app_conn.execute("SELECT set_config('app.business_id', '', true)")

    assert await app_conn.fetch("SELECT id FROM cases") == []
    assert await app_conn.fetch("SELECT id FROM businesses") == []


@pytest.mark.anyio
async def test_a01_cannot_write_rows_for_another_business(app_conn: asyncpg.Connection) -> None:
    business_a, _ = await _new_business_with_case(app_conn)
    business_b, _ = await _new_business_with_case(app_conn)

    await set_business(app_conn, business_a)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute(
            "INSERT INTO cases (business_id, kind, origin, text_enc)"
            " VALUES ($1, 'text', 'local', $2)",
            business_b,
            b"x",
        )


# --- A03 Injection: SQL parameters are always bound --------------------------------


@pytest.mark.anyio
async def test_a03_business_id_is_bound_not_interpolated(app_conn: asyncpg.Connection) -> None:
    await _new_business_with_case(app_conn)
    injected = "00000000-0000-0000-0000-000000000000' OR '1'='1"

    await set_business(app_conn, injected)  # type: ignore[arg-type]
    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        await app_conn.fetch("SELECT id FROM cases")


_SQL_CALLS = {"execute", "executemany", "fetch", "fetchrow", "fetchval"}


def _dynamic_sql_calls(source: str) -> list[int]:
    """Line numbers where a SQL call receives a query built at runtime."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _SQL_CALLS or not node.args:
            continue
        query = node.args[0]
        built = isinstance(query, ast.JoinedStr | ast.BinOp) or (
            isinstance(query, ast.Call)
            and isinstance(query.func, ast.Attribute)
            and query.func.attr == "format"
        )
        if built:
            lines.append(node.lineno)
    return lines


def test_a03_detector_flags_runtime_built_queries() -> None:
    sample = 'conn.execute(f"SELECT {x}")\nconn.fetch("SELECT " + x)\nconn.fetch("SELECT $1", x)\n'

    assert _dynamic_sql_calls(sample) == [1, 2]


def test_a03_no_runtime_built_sql_in_src() -> None:
    # Second copy of the "scan every src module" loop (also in test_i18n.py); rule of
    # three: extract it on the third use.
    offenders = {
        str(path.relative_to(SRC)): lines
        for path in SRC.rglob("*.py")
        if (lines := _dynamic_sql_calls(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}


# --- A05 Security misconfiguration: headers and no public docs in prod -------------


def test_a05_security_headers_are_set() -> None:
    response = TestClient(create_app(Settings())).get("/salud")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_a05_no_api_docs_in_prod(path: str) -> None:
    prod = Settings(
        mode="prod",
        database_url="postgresql://app@db/vigia",
        migration_database_url="postgresql://owner@db/vigia",
    )
    client = TestClient(create_app(prod))

    assert client.get(path).status_code == 404
