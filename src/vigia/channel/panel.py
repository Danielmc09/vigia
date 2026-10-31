"""Operator panel page (T-13): server-rendered HTML from non-PII rows only (D-072, D-073).

Every value goes through :func:`html.escape`; the page has no script, so the response can
ship a ``default-src 'none'`` CSP. User-facing labels come from ``i18n`` (``panel.*``).
"""

from collections.abc import Mapping, Sequence
from datetime import timedelta, timezone
from html import escape
from typing import Any, NamedTuple, get_args

from vigia.domain.verdict import Label, VerdictRow
from vigia.i18n import t

LANG = "en"  # ponytail: one operator, one language; per-operator language with T-17b
COLOMBIA = timezone(timedelta(hours=-5))  # no DST in Colombia
EXAMPLES = ("bank", "courier", "legit")
COLUMNS = ("time", "kind", "brand", "label", "confidence", "signals", "tools", "usd", "ms")
_STYLE = """
:root{color-scheme:light dark;--bg:#f5f6f8;--surface:#fff;--fg:#1b1f24;--muted:#5b6470;
--line:#e2e5e9;--accent:#1a56db;--on-accent:#fff;--zebra:#fafbfc;
--bad:#a50e0e;--bad-bg:#fde8e7;--ok:#0d652d;--ok-bg:#e6f4ea;
--maybe:#8a4b00;--maybe-bg:#fef3e0;--sig:#7a4a00;--sig-bg:#fff6e5;--sig-edge:#e3a008}
@media (prefers-color-scheme: dark){:root{--bg:#0f1115;--surface:#171a20;--fg:#e6e8eb;
--muted:#9aa3ad;--line:#2a2f37;--accent:#7aa2ff;--on-accent:#0b1020;--zebra:#1b1f26;
--bad:#ffb4ab;--bad-bg:#3b1715;--ok:#8fd6a3;--ok-bg:#12321d;
--maybe:#ffc77a;--maybe-bg:#3a2a0f;--sig:#ffd28a;--sig-bg:#2e2412;--sig-edge:#b7791f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1280px;margin:0 auto;padding:24px;display:grid;
grid-template-columns:minmax(0,1fr);gap:24px}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:0 0 12px}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px}
.today{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px}
.try{border:2px solid var(--accent)}
.try h2{font-size:20px}
.note{color:var(--muted);font-size:13px;margin:8px 0 0}
textarea{width:100%;min-height:96px;padding:12px;font:inherit;color:inherit;
background:var(--bg);border:1px solid var(--line);border-radius:8px;resize:vertical}
textarea:focus,button:focus-visible,input:focus-visible{outline:2px solid var(--accent);
outline-offset:2px}
.actions{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
gap:12px;margin-top:12px}
label{color:var(--muted);font-size:13px}
input[type=file]{display:block;margin-top:4px;font:inherit;font-size:13px;color:var(--muted)}
button{font:inherit;cursor:pointer;min-height:40px;border-radius:8px}
.primary{background:var(--accent);color:var(--on-accent);border:0;padding:8px 28px;
font-weight:600}
.examples{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:16px;
padding-top:16px;border-top:1px solid var(--line)}
.examples span{color:var(--muted);font-size:13px}
.quick{background:transparent;color:var(--accent);border:1px solid var(--accent);
padding:4px 14px;border-radius:999px;font-size:13px}
.scroll{overflow:auto;max-height:70vh;border:1px solid var(--line);border-radius:12px;
background:var(--surface)}
.try .scroll{margin-top:16px;max-height:none}
table{width:100%;min-width:820px;border-collapse:collapse}
th,td{text-align:left;padding:10px 12px;vertical-align:top;border-bottom:1px solid var(--line)}
thead th{position:sticky;top:0;z-index:1;background:var(--surface);color:var(--muted);
font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
tbody tr:nth-child(even){background:var(--zebra)}
tbody tr:last-child td{border-bottom:0}
.c-time,.c-confidence,.c-usd,.c-ms{white-space:nowrap;font-variant-numeric:tabular-nums}
.c-confidence,.c-usd,.c-ms{text-align:right}
.tag{display:inline-block;white-space:nowrap;border:1px solid;border-radius:6px;
padding:1px 8px;font-size:13px;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:4px}
.chip{border-radius:4px;padding:1px 6px;font-size:12px;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.sig{color:var(--sig);background:var(--sig-bg);border-left:3px solid var(--sig-edge)}
.tool{color:var(--muted);border:1px solid var(--line)}
.warn{color:var(--bad);background:var(--bad-bg);border:1px dashed var(--bad)}
.warn::before{content:"!";margin-right:.35em}
.metrics h2{font-size:14px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.card{border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:12px;
color:var(--muted);overflow-wrap:anywhere}
.card b{display:block;font-size:16px;color:var(--fg);font-variant-numeric:tabular-nums}
.today .card{display:flex;gap:6px;align-items:baseline;border-radius:999px;padding:2px 12px;
background:var(--surface)}.today .card b{font-size:14px}
@media (max-width:700px){main{padding:16px;gap:16px}.panel{padding:16px}
.primary{width:100%}.actions{align-items:stretch}}
"""
# Labels differ by glyph and border too, not only by color (color-blind readers).
# same order as Label (scam, legit, doubtful) — strict=True below only checks the count
_TAGS = (("bad", "\\2715", "solid"), ("ok", "\\2713", "solid"), ("maybe", "?", "dashed"))
_STYLE += "".join(
    f".l-{label}{{color:var(--{tone});background:var(--{tone}-bg);border-style:{edge}}}"
    f'.l-{label}::before{{content:"{glyph}";margin-right:.35em}}'
    for label, (tone, glyph, edge) in zip(get_args(Label), _TAGS, strict=True)
)


def _card(title: str, value: object) -> str:
    return f'<div class="card">{escape(title)}<b>{escape(str(value))}</b></div>'


def _chips(names: Sequence[str], kind: str) -> str:
    return "".join(f'<span class="chip {kind}">{escape(name)}</span>' for name in names)


def _metric(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _metrics(result: Mapping[str, Any] | None) -> str:
    """Cards with the scalar metrics of the latest eval result, or a note if there is none."""
    if not result:
        return f'<p class="note">{escape(t(LANG, "panel.no_metrics"))}</p>'
    scalars = {
        name: value
        for name, value in (result.get("metricas") or {}).items()
        if isinstance(value, int | float | str)
    }
    date = f" · {escape(str(result['fecha']))}" if result.get("fecha") else ""
    cards = "".join(_card(name, _metric(value)) for name, value in scalars.items())
    return (
        f'<section class="metrics"><h2>{escape(t(LANG, "panel.metrics"))}{date}</h2>'
        f'<div class="cards">{cards}</div></section>'
    )


def _row(row: VerdictRow) -> str:
    label = escape(row.label)
    tools = _chips(row.tools, "tool") + _chips(row.alert_rejections, "warn")
    cells = (
        escape(row.created_at.astimezone(COLOMBIA).strftime("%m-%d %H:%M")),
        escape(row.kind or ""),
        escape(row.brand or ""),
        f'<span class="tag l-{label}">{escape(t(LANG, f"verdict.label.{row.label}"))}</span>',
        "" if row.confidence is None else f"{row.confidence:.2f}",
        f'<div class="chips">{_chips(row.signal_types, "sig")}</div>',
        f'<div class="chips">{tools}</div>',
        f"{row.usd:.4f}",
        str(row.latency_ms),
    )
    return (
        "<tr>"
        + "".join(
            f'<td class="c-{col}">{cell}</td>' for col, cell in zip(COLUMNS, cells, strict=True)
        )
        + "</tr>"
    )


class TryForm(NamedTuple):
    """The "try it" block of the panel.

    Attributes:
        text: Text just tried, put back in the form.
        row: Its verdict, shown above the table.
        rules_only: True when no model is configured: the verdict comes from rules alone.
        notice: Why an uploaded file could not be read.
        read: What the reader made of an uploaded file.
        links: Links decoded from its QR.
    """

    text: str = ""
    row: VerdictRow | None = None
    rules_only: bool = False
    notice: str = ""
    read: str = ""
    links: tuple[str, ...] = ()


def _try(head: str, form: TryForm) -> str:
    """The "try it" form: plain HTML posting to ``/panel``, so the CSP stays script-free."""
    examples = "".join(
        '<button class="quick" name="text" '
        f'value="{escape(t(LANG, f"panel.try.example.{name}.text"))}">'
        f"{escape(t(LANG, f'panel.try.example.{name}'))}</button>"
        for name in EXAMPLES
    )
    row = form.row
    result = row and (
        f'<div class="scroll"><table class="tried"><thead><tr>{head}</tr></thead>'
        f"<tbody>{_row(row)}</tbody></table></div>"
    )
    scope = form.rules_only and f'<p class="note">{escape(t(LANG, "panel.try.rules_only"))}</p>'
    notes = (
        (form.notice, form.notice),
        (form.read, f"{t(LANG, 'panel.try.read')}: {form.read}"),
        (form.links, f"{t(LANG, 'panel.try.links')}: {' '.join(form.links)}"),
    )
    shown = "".join(f'<p class="note">{escape(note)}</p>' for present, note in notes if present)
    return (
        f'<section class="panel try"><h2>{escape(t(LANG, "panel.try.title"))}</h2>'
        '<form method="post" action="/panel" enctype="multipart/form-data">'
        f'<textarea name="text" rows="3">{escape(form.text)}</textarea><div class="actions">'
        f"<label>{escape(t(LANG, 'panel.try.file'))} "
        '<input type="file" name="file" accept="audio/*,image/*,application/pdf"></label>'
        f'<button class="primary">{escape(t(LANG, "panel.try.button"))}</button></div></form>'
        f'<form class="examples" method="post" action="/panel">'
        f"<span>{escape(t(LANG, 'panel.try.examples'))}</span>{examples}</form>"
        f"{scope or ''}{shown}{result or ''}</section>"
    )


def render_panel(
    rows: Sequence[VerdictRow],
    today: Mapping[str, int],
    metrics: Mapping[str, Any] | None,
    form: TryForm | None = None,
) -> str:
    """The whole panel page.

    Args:
        rows: Latest verdicts, newest first (no PII by construction, D-072).
        today: Verdicts since midnight in Colombia, by label.
        metrics: The latest eval result file (``metricas``, ``fecha``), if there is one.
        form: The "try it" block: the text just tried, its verdict, and whether rules only.

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
        f'<header class="top"><h1>{title}</h1><div class="today">'
        f"{escape(t(LANG, 'panel.today'))}{counters}</div></header>"
        f"{_try(head, form or TryForm())}"
        f'<section><h2>{escape(t(LANG, "panel.verdicts"))}</h2><div class="scroll">'
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>"
        f"{_metrics(metrics)}"
        "</main></body></html>"
    )
