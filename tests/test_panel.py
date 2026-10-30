"""T-13 operator panel: non-PII verdict rows across businesses, token gate, escaping (D-072/73)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient

from vigia.app import MAX_CASE_CHARS, PANEL_ROWS, create_app
from vigia.channel.panel import TryForm, render_panel
from vigia.config import Settings
from vigia.domain.case import Case
from vigia.domain.member import Business
from vigia.domain.signal import Signal
from vigia.domain.verdict import Verdict, VerdictRow
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

TOKEN = "t" * 40
PHONE = "+573000000777"
EMAIL = "rosa.ficticia@correo.example"
TEXT = "Hola Rosa, soy Bancolombia, ingresa a bancolombia-seguro.com con tu clave"


def _case(business_id: object, kind: str = "text") -> Case:
    return Case(
        id=uuid4(),
        business_id=business_id,  # type: ignore[arg-type]
        country="CO",
        origin="local",
        sender=PHONE,
        kind=kind,  # type: ignore[arg-type]
        text=TEXT,
        received_at=datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
    )


def _verdict(case: Case, brand: str | None = "Bancolombia") -> Verdict:
    return Verdict(
        case_id=str(case.id),
        business_id=str(case.business_id),
        label="scam",
        confidence=0.9,
        signals=[
            Signal(
                type="brand_off_domain",
                weight="hard",
                source="urls",
                evidence={"url": "bancolombia-seguro.com", "sender": PHONE},
            ),
            Signal(type="analyst_secret_hint", weight="medium", source="analyst"),
        ],
        impersonated_brand=brand,
        user_explanation=f"{TEXT} {EMAIL}",
        action="dont_open_link",
        tools_used=[
            {"name": "analyze_url", "ms": 10, "usd": 0.0},
            {"name": "muse-spark-1.3", "ms": 900, "usd": 0.003},
            {"name": "muse-spark-1.3", "ms": 800, "usd": 0.002},
        ],
        degraded=False,
        total_latency_ms=1750,
    )


async def _business_with_verdict(
    conn: asyncpg.Connection, keyring: Keyring, brand: str | None = "Bancolombia"
) -> Verdict:
    business = Business(id=uuid4(), country="CO", language="es")
    vault = await store.create_business(conn, keyring, business, "Ferreteria Ficticia")
    case = _case(business.id)
    await store.save_case(conn, vault, case)
    verdict = _verdict(case, brand)
    await store.save_verdict(conn, vault, case, verdict)
    return verdict


# --- store: what the operator path returns -------------------------------------------------


@pytest.mark.anyio
async def test_panel_rows_carry_only_non_pii_columns(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    await _business_with_verdict(app_conn, keyring)

    rows = await store.panel_verdicts(app_conn, PANEL_ROWS)

    row = rows[0]
    assert row.kind == "text"
    assert row.brand == "Bancolombia"
    assert row.label == "scam"
    assert row.confidence == pytest.approx(0.9)
    assert row.signal_types == ["brand_off_domain"]  # analyst types never leave (D-060)
    assert row.tools == ["analyze_url", "muse-spark-1.3"]
    assert row.usd == pytest.approx(0.005)
    assert row.latency_ms == 1750
    dumped = row.model_dump_json()
    for pii in (PHONE, EMAIL, "Rosa", "Ferreteria", "bancolombia-seguro.com"):
        assert pii not in dumped


@pytest.mark.anyio
async def test_the_full_verdict_is_stored_sealed(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    await _business_with_verdict(app_conn, keyring)

    raw = await app_conn.fetchval("SELECT payload_enc FROM verdicts")

    assert raw is not None and PHONE.encode() not in raw and b"Rosa" not in raw


@pytest.mark.anyio
async def test_operator_sees_every_business_while_rls_still_isolates_tables(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    await _business_with_verdict(app_conn, keyring, brand="Nequi")
    await _business_with_verdict(app_conn, keyring, brand="DIAN")

    rows = await store.panel_verdicts(app_conn, PANEL_ROWS)

    assert {r.brand for r in rows} >= {"Nequi", "DIAN"}
    assert await app_conn.fetchval("SELECT count(*) FROM verdicts") == 1  # RLS: own only


@pytest.mark.anyio
async def test_today_counts_verdicts_by_label(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    before = await store.verdicts_today(app_conn)
    await _business_with_verdict(app_conn, keyring)
    await _business_with_verdict(app_conn, keyring)

    after = await store.verdicts_today(app_conn)

    assert after.get("scam", 0) - before.get("scam", 0) == 2


@pytest.mark.anyio
async def test_panel_functions_are_pinned_security_definers(
    admin_conn: asyncpg.Connection,
) -> None:
    rows = await admin_conn.fetch(
        "SELECT proname, prosecdef, proconfig, prosrc FROM pg_proc"
        " WHERE proname IN ('vigia_panel_verdicts', 'vigia_panel_today')"
    )

    assert {r["proname"] for r in rows} == {"vigia_panel_verdicts", "vigia_panel_today"}
    for row in rows:
        assert row["prosecdef"] is True
        assert "search_path=public, pg_temp" in (row["proconfig"] or [])
        assert "public.verdicts" in row["prosrc"]
        assert "payload_enc" not in row["prosrc"] and "business_id" not in row["prosrc"]


# --- rendering ---------------------------------------------------------------------------


def _row(brand: str | None = "Bancolombia") -> VerdictRow:
    return VerdictRow(
        created_at=datetime(2026, 10, 28, 14, 5, tzinfo=UTC),
        kind="pdf",
        brand=brand,
        label="scam",
        confidence=0.9,
        signal_types=["brand_off_domain"],
        tools=["read_pdf"],
        usd=0.0042,
        latency_ms=5300,
    )


def test_a_hostile_brand_is_escaped() -> None:
    page = render_panel([_row("<script>alert(1)</script>")], {"scam": 1}, None)

    assert "<script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_the_page_shows_the_columns_counters_and_metrics() -> None:
    metrics = {"fecha": "2026-10-28", "metricas": {"recall_estafa": 0.95, "fp_legitimo": 0.03}}

    page = render_panel([_row()], {"scam": 3, "legit": 2}, metrics)

    for text in ("Bancolombia", "brand_off_domain", "read_pdf", "0.90", "5300", "pdf"):
        assert text in page
    assert "recall_estafa" in page and "0.95" in page
    assert "prefers-color-scheme: dark" in page
    assert "1280px" in page


def test_an_empty_panel_still_renders() -> None:
    page = render_panel([], {}, None)

    assert "<table" in page


# --- HTTP: token gate --------------------------------------------------------------------


async def _source() -> tuple[list[VerdictRow], dict[str, int]]:
    return [_row()], {"scam": 1}


def _client(
    token: str | None, metrics: Path | None = None, model_key: str = "", analyst: object = None
) -> TestClient:
    # MODEL_API_KEY="" (unset, D-087): a key in the shell must never reach real Spark here.
    values: dict[str, object] = {"VIGIA_PANEL_TOKEN": token, "MODEL_API_KEY": model_key}
    if metrics is not None:
        values["VIGIA_PANEL_METRICS"] = metrics
    return TestClient(create_app(Settings(**values), analyst=analyst, panel=_source))  # type: ignore[arg-type]


def test_panel_does_not_exist_without_a_configured_token() -> None:
    assert _client(None).get("/panel").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}],
    ids=["none", "wrong", "no-bearer"],
)
def test_panel_refuses_a_missing_or_wrong_token(headers: dict[str, str]) -> None:
    assert _client(TOKEN).get("/panel", headers=headers).status_code == 401


def test_panel_with_the_bearer_token_renders_without_caching(tmp_path: Path) -> None:
    metrics = tmp_path / "ultimo.json"
    metrics.write_text(json.dumps({"metricas": {"recall_estafa": 0.97}}), encoding="utf-8")

    response = _client(TOKEN, metrics).get("/panel", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "Bancolombia" in response.text and "0.97" in response.text


def test_panel_accepts_the_token_as_a_cookie() -> None:
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    assert client.get("/panel").status_code == 200


def test_a_browser_link_trades_the_token_for_a_cookie_and_drops_it_from_the_url() -> None:
    """T-22c incident (clean clone): `make demo` opens /panel?token=... and got 401."""
    client = _client(TOKEN)

    response = client.get(f"/panel?token={TOKEN}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/panel"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.get("/panel").status_code == 200


def test_a_browser_link_with_a_wrong_token_is_refused() -> None:
    assert _client(TOKEN).get("/panel?token=wrong", follow_redirects=False).status_code == 401


def test_a_short_panel_token_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="VIGIA_PANEL_TOKEN"):
        Settings(VIGIA_PANEL_TOKEN="short")


def test_the_image_ships_the_eval_result_the_panel_reads() -> None:
    root = Path(__file__).parents[1]
    ignore = (root / ".dockerignore").read_text("utf-8").splitlines()
    dockerfile = (root / "Dockerfile").read_text("utf-8")

    assert "!evals/ultimo*.resultado.json" in ignore
    assert "COPY evals ./evals" in dockerfile
    assert Settings().panel_metrics == Path("evals/ultimo.resultado.json")  # relative to WORKDIR


# --- T-22d: "try it" form ----------------------------------------------------------------


def test_the_page_offers_a_try_form_with_three_examples() -> None:
    page = render_panel([], {}, None)

    assert '<form method="post" action="/panel"' in page
    assert page.count('name="text" value=') == 3


def test_a_tried_verdict_is_shown_above_the_table_with_its_text_escaped() -> None:
    page = render_panel([], {}, None, TryForm(text="<b>hola</b>", row=_row()))

    assert page.index("brand_off_domain") < page.index("<table><thead>")
    assert "&lt;b&gt;hola&lt;/b&gt;</textarea>" in page


def test_the_try_form_judges_a_pasted_text_and_shows_the_verdict() -> None:
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    response = client.post("/panel", data={"text": "Ingresa a bancolombia-seguro.com ya"})

    assert response.status_code == 200
    assert "brand_off_domain" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_the_try_form_needs_the_panel_token() -> None:
    response = _client(TOKEN).post("/panel", data={"text": "hola"})

    assert response.status_code == 401


def test_an_empty_try_is_refused() -> None:
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    assert client.post("/panel", data={"text": "  "}).status_code == 422


def test_the_form_says_rules_only_when_there_is_no_model() -> None:
    assert "Rules only" in render_panel([], {}, None, TryForm(rules_only=True))
    assert "Rules only" not in render_panel([], {}, None, TryForm())


def test_without_a_model_key_the_panel_says_rules_only() -> None:
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    assert "Rules only" in client.get("/panel").text


def test_with_a_model_key_the_form_does_not_say_rules_only() -> None:
    client = _client(TOKEN, model_key="not-a-real-key")
    client.cookies.set("vigia_panel", TOKEN)

    assert "Rules only" not in client.get("/panel").text


def test_the_try_form_asks_the_analyst() -> None:
    asked: list[str] = []

    def analyst(case: Case, *_: object) -> None:
        asked.append(case.text or "")

    client = _client(TOKEN, analyst=analyst)
    client.cookies.set("vigia_panel", TOKEN)

    client.post("/panel", data={"text": "Le escribe su asesor, confirme el pago de hoy"})

    assert asked == ["Le escribe su asesor, confirme el pago de hoy"]


def test_a_try_longer_than_the_cap_is_refused() -> None:
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    assert client.post("/panel", data={"text": "a" * (MAX_CASE_CHARS + 1)}).status_code == 422


def test_a_case_longer_than_the_cap_is_refused() -> None:
    too_long = "a" * (MAX_CASE_CHARS + 1)

    response = _client(TOKEN).post("/caso", json={"country": "CO", "text": too_long})

    assert response.status_code == 422
