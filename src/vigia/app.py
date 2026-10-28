"""FastAPI application: HTTP entrypoint (application layer)."""

import asyncio
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, SecretStr

from vigia import telemetry
from vigia.channel.panel import render_panel
from vigia.channel.wazenda import signed_payload, to_message
from vigia.config import WEBHOOK_CONCURRENCY, Settings, settings
from vigia.domain.case import IncomingMessage
from vigia.domain.gate import Judge
from vigia.domain.member import Business
from vigia.domain.verdict import Verdict, VerdictRow
from vigia.model import jev, spark
from vigia.model.jev import make_judge
from vigia.services import whatsapp
from vigia.services.ingest import normalize
from vigia.services.process_case import Analyst, judge_case
from vigia.signals.knowledge import seeded_pack
from vigia.store import postgres as store

_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
_PANEL_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
log = logging.getLogger(__name__)
PANEL_COOKIE = "vigia_panel"
MAX_CLOCK_SKEW_S = 300  # specs/01: a webhook older or newer than 5 minutes is ignored
PANEL_ROWS = 50
HEALTH_TIMEOUT_S = 2.0  # specs/11 §2: a slow dependency is a failing one
HEALTH_CACHE_S = 10.0  # one real check per 10 s, however often /salud is hit

PanelSource = Callable[[], Awaitable[tuple[list[VerdictRow], dict[str, int]]]]
"""Where the panel reads its rows and today's counts; tests inject a fake (D-072)."""


class CaseRequest(BaseModel):
    """Minimal body to analyze one text case outside any real channel (D-048).

    Attributes:
        country: ISO code of the country pack to judge it with.
        text: The message, as a member would forward it.
    """

    country: str
    text: str


def _db_panel(config: Settings) -> PanelSource:
    """Panel rows from Postgres as ``vigia_app``, through the definer readers (D-072)."""

    async def read() -> tuple[list[VerdictRow], dict[str, int]]:
        # ponytail: one connection per page view; a pool arrives with the webhook (T-10).
        conn = await asyncpg.connect(config.database_url)
        try:
            rows = await store.panel_verdicts(conn, PANEL_ROWS)
            return rows, await store.verdicts_today(conn)
        finally:
            await conn.close()

    return read


def _presented_token(request: Request) -> str:
    """The operator token from ``Authorization: Bearer`` or the panel cookie, else empty."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value:
        return value
    return request.cookies.get(PANEL_COOKIE, "")


def _latest_metrics(config: Settings) -> dict[str, Any] | None:
    """The latest full eval result, or ``None`` if it is missing or unreadable."""
    try:
        data: dict[str, Any] = json.loads(config.panel_metrics.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data


def _require_token(request: Request, token: SecretStr | None) -> None:
    """404 when ``token`` is not configured, 401 unless the request presents it.

    Compared in constant time (D-073).
    """
    if token is None:
        raise HTTPException(status_code=404)
    expected = token.get_secret_value().encode()
    if not hmac.compare_digest(_presented_token(request).encode(), expected):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})


async def _panel_page(request: Request, config: Settings, source: PanelSource) -> HTMLResponse:
    """The operator panel behind the operator token (D-072, D-073).

    Args:
        request: Carries the operator token as a Bearer header or a cookie.
        config: Settings with the token and the eval result path.
        source: Where the rows and today's counts come from.

    Returns:
        The page; 404 when no token is configured, 401 without the right one.
    """
    _require_token(request, config.panel_token)
    rows, today = await source()
    page = render_panel(rows, today, _latest_metrics(config))
    return HTMLResponse(page, headers=_PANEL_HEADERS)


async def _postgres_ok(config: Settings) -> bool:
    """``SELECT 1`` within the health timeout."""
    try:
        conn = await asyncpg.connect(config.database_url, timeout=HEALTH_TIMEOUT_S)
        try:
            return bool(await conn.fetchval("SELECT 1", timeout=HEALTH_TIMEOUT_S) == 1)
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001 - any failure to reach Postgres is a failing check
        return False


async def _http_ok(http: httpx.AsyncClient, url: str, headers: dict[str, str]) -> bool:
    """Whether ``GET url`` answers below 500 (Meta also needs a 2xx, see :func:`_health`)."""
    try:
        response = await http.get(url, headers=headers, timeout=HEALTH_TIMEOUT_S)
    except httpx.HTTPError:
        return False
    return response.is_success or (not headers and response.status_code < 500)


async def _health(api: FastAPI, config: Settings) -> JSONResponse:
    """``GET /salud`` (specs/11 §2): Postgres, and Meta and Jev when this process uses them.

    Meta is ``GET /models`` with the key (must be 2xx); Jev is reachability only (any
    answer below 500), so the check spends no Jev call.

    Args:
        api: The app; ``state.health_http`` is the HTTP client (tests inject a mock).
        config: Settings that say which dependencies are in use.

    Returns:
        200 with every check ``ok`` or ``skip``; 503 when one is ``fail``.
    """
    cached = api.state.health_cache
    if cached is not None and cached[0] > time.monotonic():
        return JSONResponse(cached[1], status_code=cached[2])
    if api.state.health_http is None:
        api.state.health_http = httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S)
    http: httpx.AsyncClient = api.state.health_http
    meta_key = config.model_api_key.get_secret_value() if config.model_api_key else ""
    uses_jev = config.jev_backend == "real" and config.jev_api_key is not None

    def mark(ok: bool) -> str:
        return "ok" if ok else "fail"

    checks = {
        "postgres": mark(await _postgres_ok(config)),
        "meta": "skip",
        "jev": "skip",
    }
    if meta_key:
        auth = {"Authorization": f"Bearer {meta_key}"}
        checks["meta"] = mark(await _http_ok(http, f"{spark.BASE}/models", auth))
    if uses_jev:
        checks["jev"] = mark(await _http_ok(http, jev.URL, {}))
    failed = "fail" in checks.values()
    body = {"status": "fail" if failed else "ok", "checks": checks}
    code = 503 if failed else 200
    # A public endpoint: without this, every hit is a Postgres and a Meta round trip.
    api.state.health_cache = (time.monotonic() + HEALTH_CACHE_S, body, code)
    return JSONResponse(body, status_code=code)


async def _guarded(
    process: whatsapp.WebhookPorts, msg: IncomingMessage, slots: asyncio.Semaphore
) -> None:
    """Run one message in the background; a failure is logged by type only (no PII)."""
    try:
        # ponytail: the cap is per process; a shared queue (Redis) when there are several
        async with slots:
            await process.process(msg)
    except Exception as exc:  # noqa: BLE001 - the ack is gone; logging is all that is left
        log.error(
            "webhook_failed from=%s error=%s", telemetry.mask_phone(msg.sender), type(exc).__name__
        )


def _ack(counted: str, status: str | None = None, code: int = 200) -> JSONResponse:
    """Count a webhook outcome and answer it.

    Args:
        counted: ``vigia_webhook_total`` status label.
        status: Body status when it differs from ``counted`` (a stale message is ``ignored``).
        code: HTTP status.

    Returns:
        ``{"status": ...}``.
    """
    telemetry.count("webhook", counted)
    return JSONResponse({"status": status or counted}, status_code=code)


async def _webhook(request: Request, config: Settings) -> JSONResponse:
    """``POST /webhook/wazenda`` (specs/01): verify, filter, dedupe, ack, process later.

    Args:
        request: Raw webhook request.
        config: Settings with the webhook secret.

    Returns:
        202 accepted; 200 ignored or duplicate (business rejections are never 4xx).

    Raises:
        HTTPException: 401 when the signature does not check.
    """
    state = request.app.state
    secret = config.wazenda_webhook_secret
    raw = signed_payload(
        secret.get_secret_value().encode() if secret else None,
        await request.body(),
        request.headers.get("X-Wazenda-Signature"),
    )
    if raw is None:
        telemetry.count("webhook", "unauthorized")
        raise HTTPException(status_code=401)
    msg = to_message(raw)
    if msg is None:
        return _ack("ignored")
    if abs((datetime.now(UTC) - msg.received_at).total_seconds()) > MAX_CLOCK_SKEW_S:
        log.info("webhook_stale from=%s", telemetry.mask_phone(msg.sender))
        return _ack("stale", "ignored")
    if getattr(state, "wazenda", None) is None:
        state.wazenda = whatsapp.live(config, state.metrics)
    ports: whatsapp.WebhookPorts = state.wazenda
    if not await ports.first_time(msg.message_id):
        return _ack("duplicate")
    task = asyncio.create_task(_guarded(ports, msg, state.webhook_slots))
    tasks: set[asyncio.Task[None]] = state.webhook_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return _ack("accepted", code=202)


@asynccontextmanager
async def _lifespan(api: FastAPI) -> AsyncIterator[None]:
    """At shutdown, finish the messages already acked, then close the webhook ports."""
    yield
    await asyncio.gather(*list(api.state.webhook_tasks))  # _guarded never raises
    ports: whatsapp.WebhookPorts | None = api.state.wazenda
    if ports is not None:
        await ports.close()
    if api.state.health_http is not None:
        await api.state.health_http.aclose()


def _observe(config: Settings) -> None:
    """JSON logs in production and Sentry when ``SENTRY_DSN`` is set (specs/11 §1)."""
    if config.mode == "prod":  # ponytail: dev keeps structlog's readable console output
        telemetry.configure_logging()
    if config.sentry_dsn is not None:
        telemetry.init_sentry(config.sentry_dsn.get_secret_value())


def _operations(api: FastAPI, config: Settings) -> None:
    """Register ``/salud`` and ``/metrics`` (specs/11 §2) on ``api``."""

    @api.get("/salud")
    async def health() -> JSONResponse:
        """Health with its dependencies (specs/11 §2)."""
        return await _health(api, config)

    @api.get("/metrics")
    async def metrics(request: Request) -> Response:
        """Prometheus metrics behind ``METRICS_TOKEN`` (specs/11 §2)."""
        _require_token(request, config.metrics_token)
        return Response(generate_latest(api.state.metrics.registry), media_type=CONTENT_TYPE_LATEST)


def create_app(
    config: Settings,
    analyst: Analyst | None = None,
    judge: Judge | None = None,
    panel: PanelSource | None = None,
) -> FastAPI:
    """Build the API.

    Args:
        config: Settings for this process; ``mode == "prod"`` hides the API docs.
        analyst: Provider of the analyst's read of a case; defaults to none (degraded
            verdicts only), so tests and this endpoint never call a real model.
        judge: Jev for the manipulation gate; defaults to :func:`make_judge` (mock unless
            ``JEV_BACKEND=real`` with a key).
        panel: Source of the operator panel rows; defaults to Postgres.

    Returns:
        The configured FastAPI application.
    """
    _observe(config)
    gate_judge = judge or make_judge(config)
    panel_source = panel or _db_panel(config)
    docs = config.mode != "prod"
    api = FastAPI(
        title="VIGIA",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=_lifespan,
    )
    api.state.wazenda = None
    api.state.metrics = telemetry.Metrics()
    api.state.health_http = None
    api.state.health_cache = None
    api.state.webhook_tasks = set()
    api.state.webhook_slots = asyncio.Semaphore(WEBHOOK_CONCURRENCY)

    @api.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        return response

    _operations(api, config)

    @api.post("/caso")
    async def post_case(request: CaseRequest) -> Verdict:
        """Fuse a case into its verdict (specs/05), without any real channel or store.

        Args:
            request: Country and text of the case.

        Returns:
            The verdict, degraded unless ``analyst`` was given to :func:`create_app`.
        """
        pack = seeded_pack(request.country, config.knowledge_seed)
        if pack is None:
            raise HTTPException(status_code=422, detail=f"unknown country: {request.country!r}")
        msg = IncomingMessage(
            message_id=str(uuid4()),
            origin="local",
            sender="eval",
            kind="text",
            text=request.text,
            received_at=datetime.now(UTC),
        )
        # A throwaway business per request: nothing is stored, no tenant is touched (D-048).
        case = normalize(msg, Business(uuid4(), pack.country, pack.language))
        if case is None:
            raise HTTPException(status_code=422, detail="unsupported case")
        case = case.model_copy(update={"origin": "eval", "sender": None})
        # No chat here: one stateless text, so the gate sees no history (D-048).
        # ponytail: a real Jev call blocks the event loop up to its timeout.
        with telemetry.recording(api.state.metrics):
            return judge_case(case, pack, gate_judge, analyst)

    @api.post("/webhook/wazenda")
    async def wazenda_webhook(request: Request) -> JSONResponse:
        """Wazenda's ``message.received`` webhook (specs/01)."""
        with telemetry.recording(api.state.metrics):
            return await _webhook(request, config)

    @api.get("/panel", response_class=HTMLResponse)
    async def operator_panel(request: Request) -> HTMLResponse:
        """Operator panel: latest verdicts without PII (specs/07, D-072, D-073)."""
        return await _panel_page(request, config, panel_source)

    return api


app = create_app(settings)
