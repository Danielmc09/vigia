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
from vigia.i18n import t
from vigia.services.owner_panel import owner_view
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

OWNER_COOKIE = "vigia_owner"
LINK_CHARS = 43
"""``token_urlsafe(32)`` length; anything else is refused before touching the database."""

_HEAD = (
    '<!doctype html><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f"<title>{escape(t('es', 'panel.brand'))}</title>"
)


_REFUSAL = _HEAD + "".join(
    f"<p>{escape(t(lang, 'panel.link_invalid'))}</p>" for lang in ("es", "en")
)


def _door(link: str) -> str:
    """The page a link opens: one button, so a preview's GET spends nothing."""
    return (
        _HEAD
        + "".join(f"<p>{escape(t(lang, 'panel.enter.intro'))}</p>" for lang in ("es", "en"))
        + f'<form method="post" action="/entrar/{escape(link)}">'
        f"<button>{escape(t('es', 'panel.enter'))} / {escape(t('en', 'panel.enter'))}</button>"
        "</form>"
    )


def _refused() -> HTMLResponse:
    """One page for every bad link or session, so a refusal tells nothing apart."""
    return HTMLResponse(_REFUSAL, status_code=403, headers=PANEL_HEADERS)


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
    async def door(link: str) -> Response:
        """The page with the "Enter" button; touches nothing, so previews can open it."""
        if len(link) != LINK_CHARS:
            return _refused()
        return HTMLResponse(_door(link), headers=PANEL_HEADERS)

    @router.post("/entrar/{link}")
    async def enter(request: Request, link: str) -> Response:
        """Trade a one-time link for a 24 h session cookie, then show the owner view."""
        if len(link) != LINK_CHARS:
            return _refused()
        session = await _with_conn(config, lambda conn: store.enter_panel(conn, link))
        if session is None:
            return _refused()
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
            return _refused()
        return HTMLResponse(render_shell("owner", found["language"]), headers=PANEL_HEADERS)

    @router.get("/api/owner")
    async def owner_api(request: Request) -> JSONResponse:
        """The owner's cases, team and suppliers (no signal names, D-111); 401 without one."""
        found = await _view(request, config)
        if found is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401, headers=PANEL_HEADERS)
        return JSONResponse(found, headers=PANEL_HEADERS)

    return router
