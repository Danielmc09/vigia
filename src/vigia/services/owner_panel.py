"""What the owner sees in the panel (T-17b): their cases, team and suppliers.

D-111: verdict codes only (label, brand, kind), never internal signal names.
"""

from dataclasses import asdict
from typing import Any

import asyncpg

from vigia.store import postgres as store
from vigia.store.crypto import Keyring

OWNER_CASES = 50
"""Cases the owner panel lists, newest first."""


async def owner_view(
    conn: asyncpg.Connection, keyring: Keyring, session: str
) -> dict[str, Any] | None:
    """The owner's panel data for a live session.

    Args:
        conn: Connection with the application role.
        keyring: Opens the business DEK.
        session: Session token from the cookie.

    Returns:
        ``business``, ``language``, ``cases``, ``team`` and ``suppliers``, or ``None``
        when the session is not a live owner's.
    """
    async with conn.transaction():
        business_id = await store.panel_session(conn, session)
        if business_id is None:
            return None
        business, vault = await store.open_business(conn, keyring, business_id)
        cases = await store.recent_verdicts(conn, limit=OWNER_CASES)
        return {
            "business": await store.business_name(conn, vault),
            "language": business.language,
            "cases": [{**c._asdict(), "at": c.at.isoformat()} for c in cases],
            # Last four digits only: enough to tell two Luises apart, not a phone book.
            "team": [
                {"name": m.name, "phone": "…" + m.phone[-4:]} for m in await store.team(conn, vault)
            ],
            "suppliers": [asdict(s) for s in await store.suppliers(conn, vault)],
        }
