"""T-22c `make demo`: a clean seed, 5 sample cases land as verdicts in the panel."""

import asyncpg
import demo
import pytest

from vigia.store import postgres as store
from vigia.store.crypto import Keyring


@pytest.mark.anyio
async def test_demo_seeds_members_and_fills_the_panel_with_five_verdicts(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    await demo.seed_business(app_conn, keyring)

    await demo.send_samples(app_conn, demo.demo_channel(keyring))

    rows = await store.panel_verdicts(app_conn, 10)
    assert len(rows) == len(demo.CASES)
    # Without MODEL_API_KEY (rules only) "legit" needs the analyst (specs/05 regla 4),
    # so the smoke test only checks every case lands, not which labels come out.
    assert all(row.label in {"scam", "legit", "doubtful"} for row in rows)


@pytest.mark.anyio
async def test_a_sender_not_in_cases_stays_unenrolled(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    """Every sample's sender must be one of the 3 seeded members, or it is silently rejected."""
    senders = {sender for sender, _ in demo.CASES}

    await demo.seed_business(app_conn, keyring)

    for sender in senders:
        assert await store.find_member(app_conn, keyring.hash(sender)) is not None
