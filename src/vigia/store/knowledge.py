"""The global known-campaign table (specs/10 §4): read by the app, written by the owner."""

from collections.abc import Iterable

import asyncpg

from vigia.domain.knowledge import KnownCampaign


def _to_bytes(fingerprint: int | None) -> bytes | None:
    return None if fingerprint is None else fingerprint.to_bytes(8, "big")


def _to_int(raw: bytes | None) -> int | None:
    return None if raw is None else int.from_bytes(raw, "big")


async def fetch_known_campaigns(conn: asyncpg.Connection, country: str) -> list[KnownCampaign]:
    """Known campaigns of one country; needs no business context (the table is global).

    Args:
        conn: Connection with the application role.
        country: ISO code.

    Returns:
        The campaigns, ready to attach to the country pack.
    """
    rows = await conn.fetch(
        "SELECT id, country, kind, brand, simhash, phash, domains, source_url, source_type,"
        " seen_date FROM known_campaigns WHERE country = $1",
        country,
    )
    return [
        KnownCampaign(
            **(dict(row) | {"simhash": _to_int(row["simhash"]), "phash": _to_int(row["phash"])})
        )
        for row in rows
    ]


async def insert_known_campaigns(
    conn: asyncpg.Connection, campaigns: Iterable[KnownCampaign]
) -> None:
    """Add campaigns to the base; the application role is refused (read only).

    Args:
        conn: Connection with the schema owner role.
        campaigns: Campaigns with their fingerprints already computed.
    """
    await conn.executemany(
        "INSERT INTO known_campaigns (id, country, kind, brand, simhash, phash, domains,"
        " source_url, source_type, seen_date) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        [
            (
                c.id,
                c.country,
                c.kind,
                c.brand,
                _to_bytes(c.simhash),
                _to_bytes(c.phash),
                list(c.domains),
                c.source_url,
                c.source_type,
                c.seen_date,
            )
            for c in campaigns
        ],
    )
