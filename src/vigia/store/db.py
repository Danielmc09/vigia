"""Business-scoped database sessions: every query runs under one business_id (RLS)."""

from uuid import UUID

import asyncpg


async def set_business(conn: asyncpg.Connection, business_id: UUID) -> None:
    """Scope the current transaction to one business; RLS policies filter by it.

    Must run inside a transaction: the setting is transaction-local (``SET LOCAL``).

    Args:
        conn: Connection opened with the application role.
        business_id: Business whose rows become visible and writable.
    """
    await conn.execute("SELECT set_config('app.business_id', $1, true)", str(business_id))
