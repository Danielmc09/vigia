"""What the owner sees in the panel (T-17b): their cases, team and suppliers.

D-111: verdict codes (label, brand, kind) and the plain words the team got on WhatsApp,
never internal signal names.
"""

from dataclasses import asdict
from typing import Any

import asyncpg

from vigia.channel.templates import reason_and_action
from vigia.domain.verdict import Verdict
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

OWNER_CASES = 50
"""Cases the owner panel lists, newest first."""


def _said(verdict: Verdict | None, lang: str) -> dict[str, str | None]:
    """The reason and what to do, as the WhatsApp reply worded them (code-built, rule 14)."""
    if verdict is None:
        return {"explanation": None, "action": None}
    explanation, action = reason_and_action(verdict, lang)
    return {"explanation": explanation, "action": action}


async def owner_view(
    conn: asyncpg.Connection, keyring: Keyring, session: str
) -> dict[str, Any] | None:
    """The owner's panel data for a live session.

    Args:
        conn: Connection with the application role.
        keyring: Opens the business DEK.
        session: Session token from the cookie.

    Returns:
        ``business``, ``language``, ``cases`` (with ``explanation`` and ``action``),
        ``team`` and ``suppliers``, or ``None``
        when the session is not a live owner's.
    """
    async with conn.transaction():
        business_id = await store.panel_session(conn, session)
        if business_id is None:
            return None
        business, vault = await store.open_business(conn, keyring, business_id)
        cases = await store.owner_cases(conn, vault, limit=OWNER_CASES)
        return {
            "business": await store.business_name(conn, vault),
            "language": business.language,
            "cases": [
                {**c._asdict(), "at": c.at.isoformat(), **_said(v, business.language)}
                for c, v in cases
            ],
            # Last four digits only: enough to tell two Luises apart, not a phone book.
            "team": [
                {"name": m.name, "phone": "…" + m.phone[-4:]} for m in await store.team(conn, vault)
            ],
            "suppliers": [asdict(s) for s in await store.suppliers(conn, vault)],
        }
