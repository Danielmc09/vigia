"""Operator panel page (T-13): server-rendered HTML from non-PII rows only (D-072, D-073).

Every value goes through :func:`html.escape`; the page has no script, so the response can
ship a ``default-src 'none'`` CSP. User-facing labels come from ``i18n`` (``panel.*``).
"""

from collections.abc import Mapping, Sequence
from datetime import timedelta, timezone
from html import escape
from typing import Any, get_args

from vigia.domain.verdict import Label, VerdictRow
from vigia.i18n import t

LANG = "en"  # ponytail: one operator, one language; per-operator language with T-17b
COLOMBIA = timezone(timedelta(hours=-5))  # no DST in Colombia
COLUMNS = ("time", "kind", "brand", "label", "confidence", "signals", "tools", "usd", "ms")
_COLORS = ("#d93025", "#188038", "#e37400")  # same order as Label
_LABEL_CSS = "".join(
    f".l-{label}{{background:{color};color:#fff}}"
    for label, color in zip(get_args(Label), _COLORS, strict=True)
)
_STYLE = (
    ":root{color-scheme:light dark;--bg:#fff;--fg:#1f1f1f;--muted:#5f6368;--line:#dadce0;"
    "--chip:#eef1f4}"
    "@media (prefers-color-scheme: dark){:root{--bg:#121212;--fg:#e8eaed;--muted:#9aa0a6;"
    "--line:#3c4043;--chip:#2a2d31}}"
    "body{margin:0;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}"
    "main{max-width:1280px;margin:0 auto;padding:24px}"
    "h1{font-size:20px}h2{font-size:15px;color:var(--muted)}"
    ".cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}"
    ".card{border:1px solid var(--line);border-radius:8px;padding:8px 14px}"
    ".card b{display:block;font-size:20px}"
    "table{width:100%;border-collapse:collapse}"
    "th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}"
    "th{color:var(--muted);font-weight:600}td.n{text-align:right}"
    ".chip{display:inline-block;background:var(--chip);border-radius:10px;padding:1px 8px;"
    "margin:1px;font-size:12px}"
    ".tag{border-radius:4px;padding:2px 8px;font-weight:600}" + _LABEL_CSS
)


def _card(title: str, value: object) -> str:
    return f'<div class="card">{escape(title)}<b>{escape(str(value))}</b></div>'


def _chips(names: Sequence[str]) -> str:
    return "".join(f'<span class="chip">{escape(name)}</span>' for name in names)


def _metric(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _metrics(result: Mapping[str, Any] | None) -> str:
    """Cards with the scalar metrics of the latest eval result, or a note if there is none."""
    if not result:
        return f"<p>{escape(t(LANG, 'panel.no_metrics'))}</p>"
    scalars = {
        name: value
        for name, value in (result.get("metricas") or {}).items()
        if isinstance(value, int | float | str)
    }
    date = f" · {escape(str(result['fecha']))}" if result.get("fecha") else ""
    cards = "".join(_card(name, _metric(value)) for name, value in scalars.items())
    return f'<h2>{escape(t(LANG, "panel.metrics"))}{date}</h2><div class="cards">{cards}</div>'


def _row(row: VerdictRow) -> str:
    label = escape(row.label)
    cells = (
        escape(row.created_at.astimezone(COLOMBIA).strftime("%m-%d %H:%M")),
        escape(row.kind or ""),
        escape(row.brand or ""),
        f'<span class="tag l-{label}">{escape(t(LANG, f"verdict.label.{row.label}"))}</span>',
        "" if row.confidence is None else f"{row.confidence:.2f}",
        _chips(row.signal_types),
        _chips(row.tools),
        f"{row.usd:.4f}",
        str(row.latency_ms),
    )
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def render_panel(
    rows: Sequence[VerdictRow],
    today: Mapping[str, int],
    metrics: Mapping[str, Any] | None,
) -> str:
    """The whole panel page.

    Args:
        rows: Latest verdicts, newest first (no PII by construction, D-072).
        today: Verdicts since midnight in Colombia, by label.
        metrics: The latest eval result file (``metricas``, ``fecha``), if there is one.

    Returns:
        A self-contained HTML document with no script.
    """
    counters = "".join(
        _card(t(LANG, f"verdict.label.{label}"), today.get(label, 0)) for label in get_args(Label)
    )
    head = "".join(f"<th>{escape(t(LANG, f'panel.col.{name}'))}</th>" for name in COLUMNS)
    body = "".join(_row(row) for row in rows) or (
        f'<tr><td colspan="{len(COLUMNS)}">{escape(t(LANG, "panel.empty"))}</td></tr>'
    )
    title = escape(t(LANG, "panel.title"))
    return (
        f'<!doctype html><html lang="{LANG}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>{_STYLE}</style></head><body><main>"
        f"<h1>{title}</h1><h2>{escape(t(LANG, 'panel.today'))}</h2>"
        f'<div class="cards">{counters}</div>{_metrics(metrics)}'
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        "</main></body></html>"
    )
