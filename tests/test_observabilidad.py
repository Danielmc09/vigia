"""T-13b: logs without PII, metrics, health with dependencies, Sentry, sentinel (specs/11)."""

import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import measure
import pytest
import sentinel
import structlog
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from structlog.testing import capture_logs

from tests.conftest import Recorder
from tests.test_webhook import EMPLOYEE, OWNER, Outbox, channel, incoming, team
from vigia import telemetry
from vigia.app import create_app
from vigia.config import Settings
from vigia.domain.case import Case
from vigia.domain.reading import Transcript
from vigia.model.spark import SparkClient
from vigia.services import whatsapp
from vigia.store.crypto import Keyring

TOKEN = "m" * 32
STRANGER = "+573000000799"
EMAIL = "pagos.ana@example.com"
SECRET_TEXT = "Transferir a la cuenta 4455667788 de Ana Torres"
DOWN = "postgresql://vigia_app:x@127.0.0.1:1/vigia"


# --- logs (specs/11 §1) ----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_webhook_case_logs_every_stage_with_one_trace_and_no_pii(
    app_conn: asyncpg.Connection,
    recorder: Recorder,
    keyring: Keyring,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await team(app_conn, recorder)
    body = f"Bancolombia: {SECRET_TEXT}. Escriba a {EMAIL} o al {STRANGER} hoy mismo"
    msg = incoming("texto", EMPLOYEE, body=body)

    with capture_logs() as logs, caplog.at_level(logging.INFO):
        await whatsapp.process(app_conn, channel(keyring, Outbox()), msg)

    stages = [e for e in logs if "stage" in e]
    assert {e["stage"] for e in stages} >= {"gate", "jev", "rules", "fusion", "policy", "reply"}
    assert len({e["trace_id"] for e in stages}) == 1
    for event in stages:
        assert {"ms", "usd", "trace_id"} <= event.keys()
    case_events = [e for e in stages if e["stage"] != "gate"]
    assert all({"case_id", "business_id"} <= e.keys() for e in case_events)
    everything = json.dumps(logs, default=str) + caplog.text
    for pii in (EMPLOYEE, OWNER, STRANGER, EMAIL, "4455667788", "Ana Torres", "Bancolombia:"):
        assert pii not in everything


def test_logs_render_as_json_and_mask_phones_and_emails() -> None:
    out = io.StringIO()
    telemetry.configure_logging(out)
    try:
        structlog.get_logger().info("probe", note=f"call {STRANGER} or {EMAIL}")
        logging.getLogger("vigia.test").warning("stdlib %s", STRANGER)
    finally:
        structlog.reset_defaults()
        telemetry.reset_logging()

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["event"] for line in lines][:1] == ["probe"]
    assert STRANGER not in out.getvalue() and EMAIL not in out.getvalue()
    assert "***0799" in lines[0]["note"]


def test_stdlib_exceptions_are_logged_without_pii() -> None:
    out = io.StringIO()
    telemetry.configure_logging(out)
    try:
        try:
            raise ValueError(f"cliente {STRANGER} {EMAIL}")
        except ValueError:
            logging.getLogger("vigia.test").exception("failed")
    finally:
        structlog.reset_defaults()
        telemetry.reset_logging()

    assert "ValueError" in out.getvalue()
    assert STRANGER not in out.getvalue() and EMAIL not in out.getvalue()


@pytest.mark.parametrize(
    ("raw", "masked"),
    [("3000001234", "***1234"), ("(415) 555-0100", "***0100"), ("+57 300 000 0799", "***0799")],
)
def test_scrub_masks_local_and_us_phone_formats(raw: str, masked: str) -> None:
    assert telemetry.scrub(f"call {raw} now") == f"call {masked} now"


def test_scrub_leaves_uuids_alone() -> None:
    case_id = "550e8400-e29b-41d4-a716-446655440000"

    assert telemetry.scrub(case_id) == case_id


def test_scrub_masks_the_panel_token_in_an_access_log_line() -> None:
    """T-22c: the panel link carries ?token= and uvicorn.access logs the full path (D-088)."""
    line = '127.0.0.1:5 - "GET /panel?token=s3cret-operator-token&x=1 HTTP/1.1" 303'

    assert telemetry.scrub(line) == '127.0.0.1:5 - "GET /panel?token=***&x=1 HTTP/1.1" 303'


def test_the_uvicorn_access_log_masks_the_panel_token() -> None:
    stream = io.StringIO()
    telemetry.configure_logging(stream)
    try:
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s"', "1.2.3.4", "GET", "/panel?token=s3cret"
        )
    finally:
        telemetry.reset_logging()

    assert "s3cret" not in stream.getvalue() and "token=***" in stream.getvalue()


# --- /metrics (specs/11 §2) --------------------------------------------------------------


def _metrics_client(token: str | None = TOKEN) -> TestClient:
    return TestClient(create_app(Settings(METRICS_TOKEN=token)))


def test_metrics_do_not_exist_without_a_configured_token() -> None:
    assert _metrics_client(None).get("/metrics").status_code == 404


def test_metrics_need_the_right_token() -> None:
    client = _metrics_client()

    assert client.get("/metrics").status_code == 401
    wrong = {"Authorization": "Bearer " + "x" * 32}
    assert client.get("/metrics", headers=wrong).status_code == 401


def test_metrics_refuse_the_token_in_the_url() -> None:
    """D-088 trades a ?token= link for a cookie on /panel only; scrapes keep Bearer."""
    assert _metrics_client().get(f"/metrics?token={TOKEN}").status_code == 401


def test_metrics_token_must_be_long() -> None:
    with pytest.raises(ValueError):
        Settings(METRICS_TOKEN="short")


def test_metrics_count_cases_webhooks_and_latency_without_business_in_histograms() -> None:
    client = _metrics_client()
    text = "Bancolombia: su cuenta sera bloqueada. Verifique en https://bancolombia-seguro.co/v"
    assert client.post("/caso", json={"country": "CO", "text": text}).status_code == 200
    assert client.post("/webhook/wazenda", content=b"{}").status_code == 401

    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    page = response.text
    for name in (
        'vigia_case_ms_bucket{le="1000.0",route="obvious"}',
        "vigia_cases_total{",
        'vigia_webhook_total{status="unauthorized"} 1.0',
        "vigia_usd_total",
        "vigia_stage_errors_total",
        "vigia_model_calls_total",
        "vigia_rejected_total",
    ):
        assert name in page
    assert 'label="scam"' in page
    histogram = [line for line in page.splitlines() if line.startswith("vigia_case_ms")]
    assert histogram and not any("business_id" in line for line in histogram)


def test_two_apps_do_not_share_a_registry() -> None:
    first, second = _metrics_client(), _metrics_client()
    first.post("/webhook/wazenda", content=b"{}")
    auth = {"Authorization": f"Bearer {TOKEN}"}

    assert 'status="unauthorized"} 1.0' in first.get("/metrics", headers=auth).text
    assert 'status="unauthorized"' not in second.get("/metrics", headers=auth).text


def test_a_failing_stage_is_counted_and_logged_then_raised() -> None:
    metrics = telemetry.Metrics()

    with capture_logs() as logs, telemetry.recording(metrics), pytest.raises(RuntimeError):
        with telemetry.stage("rules"):
            raise RuntimeError(f"{STRANGER} boom")

    assert metrics.stage_errors.labels("rules")._value.get() == 1
    assert logs[0]["stage"] == "rules" and logs[0]["error"] == "RuntimeError"
    assert STRANGER not in json.dumps(logs, default=str)


def test_model_calls_of_the_real_spark_client_are_counted_by_provider_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unavailable)
    metrics = telemetry.Metrics()

    with telemetry.recording(metrics):
        SparkClient("key-not-real").read_pdf(b"%PDF-1.4", "es")

    page = generate_latest(metrics.registry).decode()
    assert 'vigia_model_calls_total{provider="meta",status="503"} 1.0' in page


# --- /salud (specs/11 §2, specs/12 C12) -------------------------------------------------


def _meta(status: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status)))


def test_health_is_503_when_postgres_is_down() -> None:
    response = TestClient(create_app(Settings(database_url=DOWN))).get("/salud")

    assert response.status_code == 503
    assert response.json()["checks"] == {
        "postgres": "fail",
        "meta": "skip",
        "jev": "skip",
        "analyst": "none",
    }


def test_health_checks_meta_only_with_a_key_and_fails_on_its_errors() -> None:
    api = create_app(Settings(MODEL_API_KEY="key-not-real"))
    client = TestClient(api)

    api.state.health_http = _meta(200)
    ok = client.get("/salud")
    api.state.health_http, api.state.health_cache = _meta(500), None
    down = client.get("/salud")

    assert ok.status_code == 200 and ok.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "meta": "ok", "jev": "skip", "analyst": "ok"},
    }
    assert down.status_code == 503 and down.json()["checks"]["meta"] == "fail"


def test_health_checks_jev_only_when_the_backend_is_real() -> None:
    api = create_app(Settings(VIGIA_MODO="dev", JEV_BACKEND="real", JEV_API_KEY="key-not-real"))
    api.state.health_http = _meta(502)

    response = TestClient(api).get("/salud")

    assert response.status_code == 503 and response.json()["checks"]["jev"] == "fail"


def test_inc_salud_sin_analista_reports_whether_the_analyst_runs() -> None:
    """Incident inc-salud-sin-analista: production ran degraded and /salud said ok."""
    degraded = Settings(MODEL_API_KEY="key-not-real", MODEL_BACKEND="spark_open")
    api = create_app(degraded)
    api.state.health_http = _meta(200)
    none = TestClient(api).get("/salud")
    api = create_app(Settings(MODEL_API_KEY="key-not-real"))
    api.state.health_http = _meta(200)
    ok = TestClient(api).get("/salud")

    assert none.status_code == 200 and none.json()["checks"]["analyst"] == "none"
    assert ok.json()["checks"]["analyst"] == "ok"


def test_salud_checks_the_analyst_without_building_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each healthcheck built a SparkClient and leaked its httpx.Client (revisor T-10b)."""
    api = create_app(Settings(MODEL_API_KEY="key-not-real"))
    api.state.health_http = _meta(200)

    def no_client(*_: object, **__: object) -> None:
        raise AssertionError("/salud built a model client")

    monkeypatch.setattr(SparkClient, "__init__", no_client)
    response = TestClient(api).get("/salud")

    assert response.json()["checks"]["analyst"] == "ok"


def test_inc_salud_reuses_its_answer_instead_of_calling_meta_each_time() -> None:
    calls: list[httpx.Request] = []
    api = create_app(Settings(MODEL_API_KEY="key-not-real"))
    api.state.health_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    )
    client = TestClient(api)

    first, second = client.get("/salud"), client.get("/salud")

    assert first.json() == second.json() and len(calls) == 1


@pytest.mark.parametrize("raw", ["3000001234", "+57 300 000 0001", "+0573000000001"])
def test_inc_operator_phone_must_be_e164(raw: str) -> None:
    with pytest.raises(ValueError, match="E.164"):
        Settings(OPERADOR_TELEFONO=raw)


@pytest.mark.parametrize(("raw", "kept"), [("+573000000001", "+573000000001"), ("", None)])
def test_operator_phone_e164_or_blank(raw: str, kept: str | None) -> None:
    assert Settings(OPERADOR_TELEFONO=raw).operator_phone == kept


# --- Sentry (specs/11 §1) ---------------------------------------------------------------


def test_sentry_events_lose_pii_and_keep_the_case_id() -> None:
    event: dict[str, Any] = {
        "message": f"failed for {STRANGER}",
        "request": {"data": SECRET_TEXT, "headers": {"X-API-Key": "k"}, "cookies": {"a": "b"}},
        "user": {"id": "u", "email": EMAIL},
        "extra": {"who": f"{EMAIL} / {STRANGER}", "n": 3},
        "tags": {"case_id": "c-1"},
        "exception": {"values": [{"type": "ValueError", "value": f"bad Ana Torres {STRANGER}"}]},
    }

    with structlog.contextvars.bound_contextvars(case_id="c-2"):
        clean = telemetry.before_send(event, {})

    assert clean is not None
    dump = json.dumps(clean)
    for pii in (STRANGER, EMAIL, SECRET_TEXT, "X-API-Key", "Ana Torres"):
        assert pii not in dump
    assert clean["exception"]["values"][0]["type"] == "ValueError"
    assert "user" not in clean and "data" not in clean.get("request", {})
    assert clean["tags"]["case_id"] == "c-2"
    assert clean["extra"]["n"] == 3


# --- sentinel (specs/11 §3) -------------------------------------------------------------


class Outbound:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.sent.append(text)
        return True


def _readers() -> dict[str, Any]:
    def heard(text: str, qr: tuple[str, ...] = ()) -> Any:
        return lambda data, lang: Transcript(text=text, usd=0.0, qr_urls=qr)

    return {
        "audio": heard("Bancolombia seguridad: dicte la clave que le llego por SMS ya"),
        "imagen": heard("Actualice sus datos", ("https://bancolombia-actualiza.co/x",)),
    }


def _answer(label: str) -> measure.Classifier:
    def classify(case: Case) -> measure.Outcome:
        return measure.Outcome(label=label)  # type: ignore[arg-type]

    return classify


def _run(tmp_path: Path, classify: measure.Classifier, *extra: str) -> tuple[int, Outbound]:
    out = Outbound()
    code = sentinel.main(
        ["--estado", str(tmp_path / "state.json"), *extra], classify, _readers(), out
    )
    return code, out


def test_sentinel_is_quiet_when_the_three_canonical_cases_hold(tmp_path: Path) -> None:
    code, out = _run(tmp_path, _answer("scam"))

    assert code == 0 and out.sent == []


def test_sentinel_alerts_when_a_verdict_changes(tmp_path: Path) -> None:
    code, out = _run(tmp_path, _answer("legit"))

    assert code == 1
    assert len(out.sent) == 3
    assert all("RUNBOOK.md#sentinel-failing" in text for text in out.sent)
    assert any("c-0001" in text and "legitimo" in text for text in out.sent)


def test_sentinel_alerts_when_a_case_is_slow(tmp_path: Path) -> None:
    code, out = _run(tmp_path, _answer("scam"), "--max-ms", "0")

    assert code == 1 and len(out.sent) == 3
    assert all("sentinel_latency" in text for text in out.sent)


def test_sentinel_does_not_repeat_an_alert_within_15_minutes(tmp_path: Path) -> None:
    _run(tmp_path, _answer("legit"))
    code, out = _run(tmp_path, _answer("legit"))

    assert code == 1 and out.sent == []


def test_sentinel_test_alert_reaches_the_operator(tmp_path: Path) -> None:
    code, out = _run(tmp_path, _answer("legit"), "--alerta-prueba")

    assert code == 0
    [text] = out.sent
    assert "RUNBOOK.md#sentinel-failing" in text


class Failing(Outbound):
    """Operator channel that accepts nothing (Wazenda down)."""

    async def __call__(self, text: str) -> bool:
        self.sent.append(text)
        return False


def test_inc_centinela_envio_fallido_retries_next_run(tmp_path: Path) -> None:
    """Hypothesis: the dedupe was recorded before delivery, silencing a lost alert 15 min."""
    state = ["--estado", str(tmp_path / "state.json")]
    sentinel.main(state, _answer("legit"), _readers(), Failing())
    out = Outbound()
    code = sentinel.main(state, _answer("legit"), _readers(), out)

    assert code == 1 and len(out.sent) == 3


def test_inc_centinela_alias_runs_without_operator() -> None:
    """Hypothesis: the herramientas/ alias did not see tools/ on sys.path (exit 1, crash)."""
    repo = Path(__file__).parents[1]
    env = {**os.environ, "OPERADOR_TELEFONO": ""}
    run = subprocess.run(  # noqa: S603
        [sys.executable, str(repo / "herramientas" / "centinela.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
        timeout=60,
        check=False,
    )

    assert run.returncode == 3, run.stderr[-500:]
