"""Shared fixtures: a real Postgres (docker compose) with migrations applied."""

import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from vigia.config import Settings
from vigia.domain.case import Case, IncomingMessage
from vigia.services.ingest import Ports
from vigia.store.crypto import Keyring
from vigia.store.migrate import apply_migrations


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _where(dsn: str) -> str:
    """Host:port/db of a DSN, without credentials, for error messages."""
    parts = urlsplit(dsn)
    return f"{parts.hostname}:{parts.port}{parts.path}"


@pytest.fixture
async def admin_dsn() -> str:
    dsn = Settings().migration_database_url
    try:
        await apply_migrations(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.fail(
            f"Postgres not reachable at {_where(dsn)} ({exc!r}). "
            "Start it with `make db` (docker compose up -d --wait postgres).",
            pytrace=False,
        )
    return dsn


@pytest.fixture
async def app_conn(admin_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """Connection as the application role, inside a transaction that is rolled back."""
    conn = await asyncpg.connect(Settings().database_url)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


@pytest.fixture
async def admin_conn(admin_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """Connection as the schema owner (superuser, bypasses RLS), rolled back at the end."""
    conn = await asyncpg.connect(admin_dsn)
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


@pytest.fixture
def keyring() -> Keyring:
    """A keyring with a KEK and HMAC key generated for this test only."""
    return Keyring(kek=os.urandom(32), hmac_key=os.urandom(32))


@dataclass
class Recorder:
    """Fake outbound ports: records the emails sent and the cases handed to analysis."""

    keyring: Keyring
    emails: list[tuple[str, str]] = field(default_factory=list)
    analyzed: list[Case] = field(default_factory=list)

    async def send_email(self, to: str, body: str) -> None:
        self.emails.append((to, body))

    async def analyze(self, case: Case) -> None:
        self.analyzed.append(case)

    @property
    def ports(self) -> Ports:
        return Ports(keyring=self.keyring, send_email=self.send_email, analyze=self.analyze)


@pytest.fixture
def recorder(keyring: Keyring) -> Recorder:
    return Recorder(keyring)


def message(sender: str, text: str | None, **fields: Any) -> IncomingMessage:
    """Synthetic incoming message from the local channel (Wazenda format pending T-01)."""
    values: dict[str, Any] = {
        "message_id": f"m-{uuid4()}",
        "origin": "local",
        "sender": sender,
        "kind": "text",
        "text": text,
        "received_at": datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
    }
    return IncomingMessage(**(values | fields))


def escalation_id(reply: str | None) -> str:
    """The 8-hex escalation id inside a ``transferir`` reply."""
    found = re.search(r"\b[0-9A-F]{8}\b", reply or "")
    assert found, reply
    return found.group()
