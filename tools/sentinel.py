"""Sentinel (specs/11 §3): three canonical cases through the pipeline, alert the operator.

Usage: ``python centinela.py [--max-ms N] [--estado state.json] [--alerta-prueba]``.

Runs an obvious text, a screenshot with a QR and a short voice note through the same path
as ``measure.py``; a changed label or a slow case sends a WhatsApp alert to
``OPERADOR_TELEFONO`` with its ``RUNBOOK.md`` anchor. The same alert for the same case is
not repeated within 15 minutes. No schedule here: Railway runs it (T-19, RUNBOOK.md).

Exit codes: 0 ok, 1 an alert fired (sent or deduplicated), 3 no operator channel.
"""

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx
import measure
import structlog

from vigia import telemetry
from vigia.channel.wazenda import HTTP_TIMEOUT_S, Wazenda
from vigia.config import settings
from vigia.i18n import t

Send = Callable[[str], Awaitable[bool]]
"""Delivers one text to the operator; ``True`` once accepted."""

CANON = (("c-0001", 5000.0), ("c-0189", 25000.0), ("c-0145", 25000.0))
"""Canonical eval cases (text, QR screenshot, voice note) and their latency ceiling in ms."""

RUNBOOK = "RUNBOOK.md#sentinel-failing"
DEDUPE_S = 15 * 60  # specs/11 §3: one alert per type every 15 minutes
OPERATOR_LANG = "es"  # Angel reads the alerts in Spanish
EXIT_OK, EXIT_ALERT, EXIT_UNAVAILABLE = 0, 1, 3


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="centinela")
    parser.add_argument("--max-ms", type=float, default=None, help="latency ceiling for all")
    default_state = Path(tempfile.gettempdir()) / "vigia-sentinel.json"
    parser.add_argument("--estado", type=Path, default=default_state, help="dedupe state file")
    parser.add_argument("--alerta-prueba", action="store_true", help="send one test alert")
    return parser.parse_args(list(argv))


def check(
    classify: measure.Classifier, readers: measure.Readers, max_ms: float | None
) -> list[dict[str, Any]]:
    """Run the canonical cases and list what broke.

    Args:
        classify: System under test (the real pipeline in production).
        readers: Voice and image readers by eval ``tipo``.
        max_ms: Latency ceiling for every case; ``None`` uses each case's own.

    Returns:
        Alerts as ``{name, value, threshold, case_id, trace_id}``; empty when all held.
    """
    cases = {c["id"]: c for c in measure.load(measure.EVALS / "casos.jsonl", None)}
    alerts = []
    for case_id, ceiling in CANON:
        with telemetry.scope():
            trace = structlog.contextvars.get_contextvars()["trace_id"]
            record = measure.grade(cases[case_id], classify, measure.REPO, readers)
        ids = {"case_id": case_id, "trace_id": trace}
        limit = ceiling if max_ms is None else max_ms
        if not record["paso"]:
            got, expected = record["obtenido"] or "error", record["esperado"]
            alerts.append({"name": "sentinel_verdict", "value": got, "threshold": expected, **ids})
        if record["ms"] > limit:
            slow = {"name": "sentinel_latency", "value": record["ms"], "threshold": limit}
            alerts.append({**slow, **ids})
    return alerts


def fresh(alerts: list[dict[str, Any]], state: Path, now: float) -> list[dict[str, Any]]:
    """The alerts not sent in the last 15 minutes (:func:`mark_sent` records them).

    Args:
        alerts: What :func:`check` found.
        state: JSON file of ``"name:case_id" -> epoch seconds``.
        now: Current epoch seconds.

    Returns:
        The alerts to send now.
    """
    sent = _sent(state)
    return [a for a in alerts if now - sent.get(_key(a), 0.0) >= DEDUPE_S]


def mark_sent(alerts: list[dict[str, Any]], state: Path, now: float) -> None:
    """Record alerts as delivered, only after the channel accepted them.

    Args:
        alerts: Alerts the operator channel accepted.
        state: Same file as :func:`fresh`.
        now: Current epoch seconds.
    """
    sent = _sent(state) | {_key(a): now for a in alerts}
    state.write_text(json.dumps(sent), "utf-8")


def _key(alert: dict[str, Any]) -> str:
    return f"{alert['name']}:{alert['case_id']}"


def _sent(state: Path) -> dict[str, float]:
    try:
        sent: dict[str, float] = json.loads(state.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return sent


def _operator() -> Send | None:
    """WhatsApp to ``OPERADOR_TELEFONO`` through Wazenda, or ``None`` if not configured."""
    phone, key, line = settings.operator_phone, settings.wazenda_api_key, settings.wazenda_line_id
    if not (phone and key and line):
        return None

    async def send(text: str) -> bool:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
            wazenda = Wazenda(settings.wazenda_base_url, key.get_secret_value(), line, http)
            return await wazenda.send(phone, text)

    return send


async def _deliver(send: Send, texts: list[str]) -> bool:
    results = [await send(text) for text in texts]
    return all(results)


def main(
    argv: Sequence[str],
    classify: measure.Classifier | None = None,
    readers: measure.Readers | None = None,
    send: Send | None = None,
) -> int:
    """Run the sentinel once.

    Args:
        argv: Command-line arguments.
        classify: System under test; defaults to the full pipeline (rules only without a key).
        readers: Readers by eval ``tipo``; default to the real ones.
        send: Operator channel; defaults to WhatsApp via Wazenda. Tests inject a fake.

    Returns:
        0 ok, 1 an alert fired, 3 no operator channel configured.
    """
    args = _parse(argv)
    send = send or _operator()
    if send is None:
        print("centinela: needs OPERADOR_TELEFONO and WAZENDA_* in the env", file=sys.stderr)
        return EXIT_UNAVAILABLE
    if args.alerta_prueba:
        with telemetry.scope():
            trace = structlog.contextvars.get_contextvars()["trace_id"]
        text = t(OPERATOR_LANG, "operator.test_alert", trace_id=trace, runbook=RUNBOOK)
        return EXIT_OK if asyncio.run(_deliver(send, [text])) else EXIT_ALERT
    classify = classify or measure.full() or measure.deterministic
    alerts = check(classify, measure.real_readers() if readers is None else readers, args.max_ms)
    now = time.time()
    due = fresh(alerts, args.estado, now)
    texts = [t(OPERATOR_LANG, "operator.alert", runbook=RUNBOOK, **alert) for alert in due]
    if texts and asyncio.run(_deliver(send, texts)):
        mark_sent(due, args.estado, now)  # a lost alert is retried on the next run
    for alert in alerts:
        print(json.dumps(alert, ensure_ascii=False))
    return EXIT_ALERT if alerts else EXIT_OK


if __name__ == "__main__":
    telemetry.configure_logging(sys.stderr)  # stdout carries only the alert JSON lines
    sys.exit(main(sys.argv[1:]))
