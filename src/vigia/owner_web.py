"""Owner panel over HTTP (T-17b): a one-time link becomes a 24 h cookie for the owner's view.

The owner never types a password: ``panel`` on WhatsApp sends ``/entrar/<token>``; that
link opens a session once and the cookie carries it. Separate from the operator's token.
Only the POST behind the page's button spends the link: WhatsApp GETs every link it sends
to draw a preview, and that GET must not burn it.
"""

from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

import asyncpg
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from vigia.channel.panel import PANEL_HEADERS, render_shell, served_over_https
from vigia.config import Settings
from vigia.i18n import LANGUAGES, t
from vigia.services.owner_panel import owner_view
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

OWNER_COOKIE = "vigia_owner"
LINK_CHARS = 43
"""``token_urlsafe(32)`` length; anything else is refused before touching the database."""
_LINK_MINUTES = int(store.PANEL_LINK_TTL.total_seconds() // 60)

# ponytail: same tokens as channel/panel.py's _STYLE, duplicated rather than shared — this
# page is a standalone card with nothing else in common with the table-based panel style.
_CARD_STYLE = """
:root{color-scheme:light dark;--bg:#f5f6f8;--surface:#fff;--fg:#1b1f24;--muted:#5b6470;
--line:#e2e5e9;--accent:#1a56db;--on-accent:#fff}
@media (prefers-color-scheme: dark){:root{--bg:#0f1115;--surface:#171a20;--fg:#e6e8eb;
--muted:#9aa3ad;--line:#2a2f37;--accent:#7aa2ff;--on-accent:#0b1020}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",
Roboto,sans-serif;padding:24px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:32px 24px;max-width:420px;width:100%;text-align:center}
h1{font-size:20px;margin:0 0 16px}
p{margin:0 0 20px}
button{font:inherit;cursor:pointer;width:100%;min-height:48px;border-radius:8px;border:0;
background:var(--accent);color:var(--on-accent);font-size:16px;font-weight:600}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.fine{margin:16px 0 0;font-size:13px;color:var(--muted)}
"""


def _lang(request: Request) -> str:
    """The language this page shows, picked from ``Accept-Language``, never the database.

    The GET must touch nothing (D-122: a WhatsApp preview's GET must not spend the link),
    so the business's own language is out of reach here.
    """
    header = request.headers.get("accept-language", "")
    for tag in header.split(","):
        code = tag.split(";", 1)[0].strip()[:2].lower()
        if code in LANGUAGES:
            return code
    return "es"


def _card(lang: str, body: str) -> str:
    """A centered card in one language: the page's whole visual vocabulary."""
    return (
        f'<!doctype html><html lang="{escape(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{escape(t(lang, 'panel.brand'))}</title><style>{_CARD_STYLE}</style></head>"
        f'<body><div class="card"><h1>{escape(t(lang, "panel.brand"))}</h1>{body}</div>'
        "</body></html>"
    )


def _door(lang: str, link: str) -> str:
    """The page a link opens: one button, so a preview's GET spends nothing."""
    return _card(
        lang,
        f"<p>{escape(t(lang, 'panel.enter.intro'))}</p>"
        f'<form method="post" action="/entrar/{escape(link)}">'
        f'<button>{escape(t(lang, "panel.enter"))}</button></form>'
        f'<p class="fine">{escape(t(lang, "panel.enter.ttl", minutes=_LINK_MINUTES))}</p>',
    )


def _refused(lang: str) -> HTMLResponse:
    """One page for every bad link or session, so a refusal tells nothing apart."""
    body = f"<p>{escape(t(lang, 'panel.link_invalid'))}</p>"
    return HTMLResponse(_card(lang, body), status_code=403, headers=PANEL_HEADERS)


async def _with_conn[T](config: Settings, work: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
    conn = await asyncpg.connect(config.database_url)
    try:
        return await work(conn)
    finally:
        await conn.close()


async def _view(request: Request, config: Settings) -> dict[str, Any] | None:
    """The owner view of the session cookie; None without a live owner session."""
    session = request.cookies.get(OWNER_COOKIE, "")
    if len(session) != LINK_CHARS:
        return None
    if request.app.state.keyring is None:
        request.app.state.keyring = Keyring.from_settings(config)
    keyring: Keyring = request.app.state.keyring
    return await _with_conn(config, lambda conn: owner_view(conn, keyring, session))


def owner_router(config: Settings) -> APIRouter:
    """Routes of the owner view: ``/entrar/{token}``, ``/dueno`` and ``/api/owner``.

    Args:
        config: Database URL and key settings.

    Returns:
        A router whose only credential is the ``vigia_owner`` session cookie.
    """
    router = APIRouter()

    @router.get("/entrar/{link}", response_class=HTMLResponse)
    async def door(request: Request, link: str) -> Response:
        """The page with the "Enter" button; touches nothing, so previews can open it."""
        lang = _lang(request)
        if len(link) != LINK_CHARS:
            return _refused(lang)
        return HTMLResponse(_door(lang, link), headers=PANEL_HEADERS)

    @router.post("/entrar/{link}")
    async def enter(request: Request, link: str) -> Response:
        """Trade a one-time link for a 24 h session cookie, then show the owner view."""
        lang = _lang(request)
        if len(link) != LINK_CHARS:
            return _refused(lang)
        session = await _with_conn(config, lambda conn: store.enter_panel(conn, link))
        if session is None:
            return _refused(lang)
        response = RedirectResponse("/dueno", status_code=303, headers=PANEL_HEADERS)
        response.set_cookie(
            OWNER_COOKIE,
            session,
            max_age=int(store.PANEL_SESSION_TTL.total_seconds()),
            httponly=True,
            samesite="lax",
            secure=served_over_https(request),
        )
        return response

    @router.get("/dueno", response_class=HTMLResponse)
    async def owner_page(request: Request) -> Response:
        """The React owner view, in the business's language; the refusal page without one."""
        found = await _view(request, config)
        if found is None:
            return _refused(_lang(request))
        return HTMLResponse(render_shell("owner", found["language"]), headers=PANEL_HEADERS)

    @router.get("/api/owner")
    async def owner_api(request: Request) -> JSONResponse:
        """The owner's cases, team and suppliers (no signal names, D-111); 401 without one."""
        found = await _view(request, config)
        if found is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401, headers=PANEL_HEADERS)
        return JSONResponse(found, headers=PANEL_HEADERS)

    return router
