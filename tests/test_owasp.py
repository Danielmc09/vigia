"""OWASP Web Top 10 checks that apply from T-00 (specs/10 section 5)."""

import ast
import io
import json
import logging
import logging.config
import re
from pathlib import Path
from uuid import uuid4

import asyncpg
import measure
import pytest
import structlog
import uvicorn.config
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from tests import test_panel_dueno
from tests.conftest import Recorder
from tests.test_panel_dueno import _business, _client, _link
from vigia import telemetry
from vigia.app import create_app
from vigia.channel.templates import team_alert_message, whatsapp_message
from vigia.config import Settings
from vigia.domain.gate import mock_judge
from vigia.services.owner_panel import owner_view
from vigia.services.process_case import judge_case
from vigia.signals.knowledge import seeded_pack
from vigia.store import postgres as store
from vigia.store.crypto import Keyring
from vigia.store.db import set_business

committed = test_panel_dueno.committed  # a fixture is registered by its module name

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


@pytest.mark.anyio
async def test_a01_an_owner_session_shows_only_its_own_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """T-17b: the owner panel is scoped by the session, never by anything the browser sends."""
    await _business(app_conn, recorder, "+573000000231")
    await _business(app_conn, recorder, "+573000000211")
    session = await store.enter_panel(app_conn, await _link(app_conn, recorder, "+573000000211"))
    assert session is not None

    view = await owner_view(app_conn, recorder.keyring, session)

    assert view is not None
    assert [m["phone"] for m in view["team"]] == ["…0211", "…0212"]
    assert len(view["cases"]) == 1


@pytest.mark.anyio
async def test_a01_the_owner_cookie_never_opens_the_operator_view(
    committed: str, keyring: Keyring
) -> None:
    owner = _client(keyring)
    owner.post(f"/entrar/{committed}", follow_redirects=False)

    assert owner.get("/api/operator").status_code == 401
    assert owner.post("/api/operator/try", data={"text": "x"}).status_code == 401
    assert owner.get("/app").status_code == 401


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


def test_a05_public_texts_never_name_internal_signals() -> None:
    """WhatsApp replies and the team alert hide the signal taxonomy (specs/10 §7, D-111).

    The panel and ``/caso`` are out of scope: operator token and tool contract.
    """
    verdicts = []
    for raw in measure.load(measure.EVALS / "casos.jsonl", None):
        if measure.needs_reading(raw):
            continue
        case = measure.to_case(raw)
        pack = seeded_pack(case.country, measure.KNOWLEDGE)
        if pack is not None:
            verdicts.append((case, pack.language, judge_case(case, pack, mock_judge, None)))
    # One-word types (urgency, prize) are plain English the i18n explanation uses on
    # purpose; only compound identifiers give the taxonomy away.
    taxonomy = {s.type for _, _, v in verdicts for s in v.signals if "_" in s.type}
    taxonomy.add("MODEL_API_KEY")
    forbidden = re.compile(r"\b(" + "|".join(map(re.escape, sorted(taxonomy))) + r")\b")

    leaks = []
    for case, lang, verdict in verdicts:
        texts = [whatsapp_message(verdict, lang)]
        if verdict.label == "scam":
            texts.append(team_alert_message(lang, "Ana", verdict, case))
        leaks += [(case.text[:40], m) for text in texts for m in forbidden.findall(text)]

    assert len(verdicts) > 50
    assert leaks == []


# --- A07 Identification failures: the owner's one-time link and its session -------


@pytest.mark.anyio
async def test_a07_a_forged_session_is_refused_and_tls_makes_the_cookie_secure(
    committed: str, keyring: Keyring
) -> None:
    forged = _client(keyring)
    forged.cookies.set("vigia_owner", "A" * 43)
    behind_tls = _client(keyring)

    entered = behind_tls.post(
        f"/entrar/{committed}", headers={"x-forwarded-proto": "https"}, follow_redirects=False
    )

    assert forged.get("/api/owner").status_code == 401
    assert forged.get("/dueno").status_code == 403
    assert entered.status_code == 303
    assert "secure" in entered.headers["set-cookie"].lower()
    assert len(committed) == 43  # token_urlsafe(32): 256 bits, nothing to guess


# --- A09 Logging failures: every case event is traceable and carries no PII --------


def test_a09_case_logs_carry_ids_but_no_pii() -> None:
    pii = ("+573000000799", "ana.torres@example.com", "Ana Torres")
    text = f"{pii[2]} {pii[0]} {pii[1]}: Bancolombia bloqueo, verifique en https://bancolombia-seguro.co/v"

    with capture_logs() as logs:
        response = TestClient(create_app(Settings())).post(
            "/caso", json={"country": "CO", "text": text}
        )

    assert response.status_code == 200
    assert logs and all({"trace_id", "case_id", "business_id"} <= e.keys() for e in logs)
    dump = json.dumps(logs, default=str)
    assert not [value for value in pii if value in dump]


def test_a09_uvicorn_errors_are_json_without_pii() -> None:
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).propagate) for n in names}
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)  # what uvicorn does at start
    out = io.StringIO()
    telemetry.configure_logging(out)
    try:
        try:
            raise ValueError("Ana Torres +573000000799")
        except ValueError:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    finally:
        structlog.reset_defaults()
        telemetry.reset_logging()
        for name, (handlers, propagate) in saved.items():
            logging.getLogger(name).handlers[:] = handlers
            logging.getLogger(name).propagate = propagate

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines and lines[0]["event"] == "Exception in ASGI application"
    assert "ValueError" in out.getvalue()
    for pii in ("Ana Torres", "+573000000799", "0799"):
        assert pii not in out.getvalue()


# --- Rule 10: tests only use synthetic phone numbers (incident inc-numero-real) -----

_CO_MOBILE = re.compile(r"(?:\+?57[\s (]*)?3\d\d[\s ().-]*\d{3}[\s .-]*\d{4}")
_SYNTHETIC = "300000"  # +57 300 000 xxxx is the only range tests may use


def _real_looking_numbers(source: str) -> list[str]:
    hits = (re.sub(r"\D", "", m) for m in _CO_MOBILE.findall(source))
    return [d for d in hits if not d.removeprefix("57").startswith(_SYNTHETIC)]


def test_rule10_detector_flags_every_phone_shape() -> None:
    """The detector catches a non-synthetic mobile in every shape WhatsApp produces."""
    for shape in (
        "+573999999999",
        "573999999999",
        "‪+57 399 999 9999‬",
        "+57 (399) 999-9999",
        "3999999999",
    ):
        assert _real_looking_numbers(shape), shape
    assert _real_looking_numbers("+57 300 000 0703 y 3000000703") == []


def test_rule10_tests_use_only_synthetic_phone_numbers() -> None:
    """Incident inc-numero-real: a fixture used the live test member's real number."""
    tests = Path(__file__).parent
    others = (p for p in tests.glob("*.py") if p.name != Path(__file__).name)
    found = {p.name: n for p in others if (n := _real_looking_numbers(p.read_text()))}
    assert found == {}
