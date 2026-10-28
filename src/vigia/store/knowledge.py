"""The global known-campaign table (specs/10 §4): read by the app, written by the owner."""

from collections.abc import Iterable

import asyncpg

from vigia.domain.knowledge import KnownCampaign
from vigia.store.crypto import Hashed


def _to_bytes(fingerprint: int | None) -> bytes | None:
    return None if fingerprint is None else fingerprint.to_bytes(8, "big")


def _to_int(raw: bytes | None) -> int | None:
    return None if raw is None else int.from_bytes(raw, "big")


SHARED_AFTER = 2  # D-082: an owner-confirmed campaign reaches everyone after 2 businesses


async def fetch_known_campaigns(
    conn: asyncpg.Connection, country: str, business: Hashed | None = None
) -> list[KnownCampaign]:
    """Known campaigns of one country; needs no business context (the table is global).

    A campaign confirmed by owners counts only for the businesses that confirmed it until
    ``SHARED_AFTER`` different ones did, so one fake business cannot poison the rest (D-082).

    Args:
        conn: Connection with the application role.
        country: ISO code.
        business: ``Keyring.hash`` of the business id asking; ``None`` sees only the shared.

    Returns:
        The campaigns, ready to attach to the country pack.
    """
    rows = await conn.fetch(
        "SELECT id, country, kind, brand, simhash, phash, domains, source_url, source_type,"
        " seen_date FROM known_campaigns WHERE country = $1 AND (source_type <> 'usuario'"
        " OR cardinality(confirmed_by) >= $2 OR $3 = ANY (confirmed_by))",
        country,
        SHARED_AFTER,
        business,
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


async def confirm_campaign(
    conn: asyncpg.Connection, campaign: KnownCampaign, business: Hashed
) -> None:
    """Add or count again a campaign an owner confirmed (``estafa confirmada``, specs/10 §4).

    Only fingerprints, domains and brand cross businesses (D-017); the definer function
    forces ``source_type = 'usuario'`` and never touches a published campaign.

    Args:
        conn: Connection with the application role.
        campaign: Fingerprints of the confirmed case; its ``id`` derives from them.
        business: ``Keyring.hash`` of the confirming business id.
    """
    await conn.execute(
        "SELECT vigia_confirm_campaign($1, $2, $3, $4, $5, $6, $7)",
        campaign.id,
        campaign.country,
        campaign.kind,
        campaign.brand,
        _to_bytes(campaign.simhash),
        list(campaign.domains),
        business,
    )
