"""Observability (specs/11): JSON logs without PII, per-app metrics, Sentry scrubbing.

A case's events share one ``trace_id`` (D-084: no OpenTelemetry); every stage logs its
``ms`` and ``usd``. Metrics reach the code that runs the case through a context variable,
so no pipeline signature grows; outside :func:`recording` nothing is counted.
"""

import logging
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal, TextIO
from uuid import uuid4

import httpx
import sentry_sdk
import structlog
from prometheus_client import CollectorRegistry, Counter, Histogram

from vigia.domain.verdict import Verdict

Stage = Literal["gate", "jev", "rules", "analyst", "fusion", "policy", "reply"]
"""Stages of specs/11 §1, in English (specs/13)."""

CASE_MS_BUCKETS = (100, 250, 500, 1000, 2000, 5000, 10000, 20000, 30000, 60000)
_PHONE = re.compile(r"(?<![\w-])[+(]?\d[\d ()\-]{4,}(\d{4})(?!\w)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_log = structlog.get_logger("vigia")


class Metrics:
    """Prometheus instruments of one app, on its own registry (never the global one).

    Attributes:
        registry: What ``GET /metrics`` exposes.
        case_ms: Case latency by ``route`` (``obvious`` without analyst, ``analyst``).
        cases: Cases by ``label``, ``degraded`` and ``business_id``.
        stage_errors: Exceptions by ``stage``.
        model_calls: Model API responses by ``provider`` (``meta``, ``jev``) and HTTP ``status``.
        usd: Model spend.
        webhook: Webhook requests by ``status``.
        rejected: Refused messages by ``reason``.
    """

    def __init__(self) -> None:
        """Create the instruments on a fresh registry."""
        reg = self.registry = CollectorRegistry()
        self.case_ms = Histogram(
            "vigia_case_ms", "Case latency in ms", ["route"], buckets=CASE_MS_BUCKETS, registry=reg
        )
        self.cases = Counter(
            "vigia_cases", "Judged cases", ["label", "degraded", "business_id"], registry=reg
        )
        self.stage_errors = Counter("vigia_stage_errors", "Stage errors", ["stage"], registry=reg)
        self.model_calls = Counter(
            "vigia_model_calls", "Model API calls", ["provider", "status"], registry=reg
        )
        self.usd = Counter("vigia_usd", "Model spend in USD", registry=reg)
        self.webhook = Counter("vigia_webhook", "Webhook requests", ["status"], registry=reg)
        self.rejected = Counter("vigia_rejected", "Refused messages", ["reason"], registry=reg)


_current: ContextVar[Metrics | None] = ContextVar("vigia_metrics", default=None)


@contextmanager
def recording(metrics: Metrics) -> Iterator[None]:
    """Count into ``metrics`` everything that runs inside (threads from ``to_thread`` too).

    Args:
        metrics: The app's instruments.

    Yields:
        Nothing.
    """
    token = _current.set(metrics)
    try:
        yield
    finally:
        _current.reset(token)


def current() -> Metrics | None:
    """The instruments being recorded into, or ``None`` (evals, scripts).

    Returns:
        The active :class:`Metrics`, if any.
    """
    return _current.get()


@contextmanager
def scope(**ids: str) -> Iterator[None]:
    """Bind ids to every log inside, reusing the ``trace_id`` already bound or making one.

    Args:
        **ids: ``case_id``, ``business_id``.

    Yields:
        Nothing.
    """
    trace = structlog.contextvars.get_contextvars().get("trace_id") or uuid4().hex
    with structlog.contextvars.bound_contextvars(trace_id=trace, **ids):
        yield


def event(name: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log one event with the bound ids (also under ``capture_logs`` in tests).

    Args:
        name: Event name, in English.
        level: ``logging`` level.
        **fields: Values without PII (counts, ids, codes, ms, usd).
    """
    _log.log(level, name, **{**structlog.contextvars.get_contextvars(), **fields})


@contextmanager
def stage(name: Stage) -> Iterator[dict[str, float]]:
    """Time one stage; log it, and count and log its exception before re-raising.

    Args:
        name: Stage of specs/11.

    Yields:
        A dict whose ``usd`` the stage may set to its spend.
    """
    cost = {"usd": 0.0}
    start = time.perf_counter()
    try:
        yield cost
    except Exception as exc:
        ms = round((time.perf_counter() - start) * 1000)
        error = type(exc).__name__
        event("stage_failed", logging.ERROR, stage=name, ms=ms, usd=cost["usd"], error=error)
        metrics = current()
        if metrics is not None:
            metrics.stage_errors.labels(name).inc()
        raise
    ms = round((time.perf_counter() - start) * 1000)
    event("stage_done", stage=name, ms=ms, usd=cost["usd"])


def count_case(verdict: Verdict, business: str, ms: float) -> None:
    """Count a judged case into the active metrics.

    Args:
        verdict: The case's verdict; ``degraded`` is the ``obvious`` route.
        business: ``business_id`` label (``eval`` outside a real business).
        ms: Case latency.
    """
    metrics = current()
    if metrics is None:
        return
    metrics.case_ms.labels("obvious" if verdict.degraded else "analyst").observe(ms)
    metrics.cases.labels(verdict.label, str(verdict.degraded).lower(), business).inc()
    metrics.usd.inc(verdict.usd)


def add_usd(usd: float) -> None:
    """Add model spend outside a judged case (attachment readers) to ``vigia_usd_total``.

    Args:
        usd: What the call cost.
    """
    metrics = current()
    if metrics is not None:
        metrics.usd.inc(usd)


def count(name: Literal["webhook", "rejected"], value: str) -> None:
    """Increment ``vigia_webhook_total{status}`` or ``vigia_rejected_total{reason}``.

    Args:
        name: Which counter.
        value: Its label.
    """
    metrics = current()
    if metrics is not None:
        getattr(metrics, name).labels(value).inc()


def _hooks(provider: str) -> Mapping[str, list[Callable[..., Any]]]:
    """``httpx`` event hooks that count each response of ``provider`` by status."""

    def count_status(response: httpx.Response) -> None:
        metrics = current()
        if metrics is not None:
            metrics.model_calls.labels(provider, str(response.status_code)).inc()

    return {"response": [count_status]}


MODEL_HOOKS = _hooks("meta")
"""``event_hooks`` for the Meta clients (Spark, SAM, Voice)."""

JEV_HOOKS = _hooks("jev")
"""``event_hooks`` for the Jev client."""


def mask_phone(phone: str) -> str:
    """A phone fit for logs, by the :func:`scrub` rule: only its last four digits.

    Args:
        phone: Normalized phone.

    Returns:
        ``***0001``; ``***`` for anything the rule does not recognize as a phone.
    """
    masked = scrub(phone)
    return masked if masked != phone else "***"


def scrub(text: str) -> str:
    """Mask phones (last four digits kept) and emails.

    Args:
        text: Any log or error text.

    Returns:
        The text with ``***1234`` for phones and ``***@***`` for emails.
    """
    return _PHONE.sub(r"***\1", _EMAIL.sub("***@***", text))


def _scrub_tree(value: Any) -> Any:
    """:func:`scrub` every string in nested dicts and lists."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_tree(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub_tree(v) for v in value]
    return value


def _exception_type(_logger: Any, _method: str, event_dict: Any) -> Any:
    """Structlog processor: an exception is logged by its type only (its message may carry PII)."""
    exc = event_dict.pop("exc_info", None)
    if exc is True:
        exc = sys.exc_info()
    if isinstance(exc, BaseException):
        event_dict["exception"] = type(exc).__name__
    elif isinstance(exc, tuple) and exc[0] is not None:
        event_dict["exception"] = exc[0].__name__
    return event_dict


def _scrub_event(_logger: Any, _method: str, event_dict: Any) -> Any:
    """Structlog processor: no phone or email leaves the process."""
    return _scrub_tree(event_dict)


_handler: logging.Handler | None = None
_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(stream: TextIO | None = None) -> None:
    """JSON logs, one per line, for structlog and the standard library, scrubbed.

    Args:
        stream: Where to write; ``None`` is the current ``sys.stdout``.
    """
    global _handler  # noqa: PLW0603 - one handler per process, replaced on reconfigure
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _exception_type,
        _scrub_event,
    ]
    structlog.configure(
        processors=[*shared, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(stream),
    )
    reset_logging()
    _handler = logging.StreamHandler(stream or sys.stdout)
    _handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(), foreign_pre_chain=shared
        )
    )
    root = logging.getLogger()
    root.addHandler(_handler)
    root.setLevel(logging.INFO)
    for name in _UVICORN:  # its own handlers print tracebacks in clear and skip the root
        server = logging.getLogger(name)
        server.handlers.clear()
        server.propagate = True


def reset_logging() -> None:
    """Remove the handler :func:`configure_logging` added to the root logger."""
    global _handler  # noqa: PLW0603 - see configure_logging
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler = None


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Sentry hook: drop the user, the request payload and exception messages, mask the rest.

    Args:
        event: Sentry event.
        hint: Sentry hint (unused).

    Returns:
        The event without PII, tagged with the bound ``case_id``.
    """
    event.pop("user", None)
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "headers", "cookies", "query_string", "env"):
            request.pop(key, None)
    for value in event.get("exception", {}).get("values", []):
        value.pop("value", None)  # the type stays, the message may carry PII (as in the logs)
    clean: dict[str, Any] = _scrub_tree(event)
    case_id = structlog.contextvars.get_contextvars().get("case_id")
    if case_id:
        clean.setdefault("tags", {})["case_id"] = case_id
    return clean


def init_sentry(dsn: str) -> None:
    """Start Sentry without PII nor local variables.

    Args:
        dsn: ``SENTRY_DSN``.
    """
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        include_local_variables=False,
        before_send=before_send,  # type: ignore[arg-type]
    )
