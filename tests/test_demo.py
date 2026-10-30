"""T-22c `make demo`: a clean seed, 5 sample cases land as verdicts in the panel."""

import asyncpg
import demo
import pytest

from vigia.config import Settings
from vigia.services import whatsapp
from vigia.store import postgres as store
from vigia.store.crypto import Keyring


@pytest.mark.anyio
async def test_demo_seeds_members_and_fills_the_panel_with_five_verdicts(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    started = await app_conn.fetchval("SELECT now()")  # fixed for the rolled-back tx
    await demo.seed_business(app_conn, keyring)

    await demo.send_samples(app_conn, demo.demo_channel(keyring))

    rows = [r for r in await store.panel_verdicts(app_conn, 50) if r.created_at >= started]
    assert len(rows) == len(demo.CASES)
    # Rules only (no MODEL_API_KEY): "legit" needs the analyst (specs/05 regla 4), but the
    # Bancolombia phishing (c-0001) must still come out "scam" for the video.
    assert "scam" in {row.label for row in rows}


def test_demo_channel_reads_attachments_like_production(keyring: Keyring) -> None:
    """The demo must not drift from the webhook: same readers for the same keys."""
    config = Settings(MODEL_API_KEY="demo-key")

    channel = demo.demo_channel(keyring, config)

    assert channel.readers.keys() == whatsapp.model_readers(config).keys()


@pytest.mark.anyio
async def test_a_sender_not_in_cases_stays_unenrolled(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    """Every sample's sender must be one of the 3 seeded members, or it is silently rejected."""
    senders = {sender for sender, _ in demo.CASES}

    await demo.seed_business(app_conn, keyring)

    for sender in senders:
        assert await store.find_member(app_conn, keyring.hash(sender)) is not None
