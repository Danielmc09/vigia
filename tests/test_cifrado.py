"""PII at rest (specs/16 section 5): ciphertext and keyed hashes only, per-business keys."""

import base64
import os
from uuid import uuid4

import asyncpg
import pytest
from cryptography.exceptions import InvalidTag

from tests.conftest import Recorder, message
from vigia.config import Settings
from vigia.services.ingest import handle
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

OWNER = "+573000000101"
EMPLOYEE = "+573000000102"
EMAIL = "ana.prueba@correo.example"
SECRET_TEXT = "Su cuenta Bancolombia fue bloqueada, ingrese a bancolombia-seguro.co ya"


# --- Unit: envelope encryption and keyed hashes -------------------------------------


def test_sealed_value_round_trips_and_is_not_plaintext(keyring: Keyring) -> None:
    vault, _ = keyring.new_business(uuid4())

    blob = vault.seal("members.phone", OWNER)

    assert OWNER.encode() not in blob
    assert vault.open("members.phone", blob) == OWNER


def test_same_value_seals_differently_each_time(keyring: Keyring) -> None:
    vault, _ = keyring.new_business(uuid4())

    assert vault.seal("cases.text", SECRET_TEXT) != vault.seal("cases.text", SECRET_TEXT)


def test_ciphertext_does_not_open_under_another_business(keyring: Keyring) -> None:
    business_a, _ = keyring.new_business(uuid4())
    business_b, _ = keyring.new_business(uuid4())

    blob = business_a.seal("cases.text", SECRET_TEXT)

    with pytest.raises(InvalidTag):
        business_b.open("cases.text", blob)


def test_ciphertext_does_not_open_in_another_column(keyring: Keyring) -> None:
    vault, _ = keyring.new_business(uuid4())

    blob = vault.seal("members.phone", OWNER)

    with pytest.raises(InvalidTag):
        vault.open("members.name", blob)


def test_wrapped_dek_reopens_only_with_the_same_kek_and_business(keyring: Keyring) -> None:
    business_id = uuid4()
    vault, wrapped = keyring.new_business(business_id)
    blob = vault.seal("cases.text", SECRET_TEXT)

    assert keyring.open_business(business_id, wrapped).open("cases.text", blob) == SECRET_TEXT
    with pytest.raises(InvalidTag):
        keyring.open_business(uuid4(), wrapped)
    with pytest.raises(InvalidTag):
        Keyring(kek=os.urandom(32), hmac_key=os.urandom(32)).open_business(business_id, wrapped)


def test_lookup_hash_is_stable_keyed_and_not_plaintext(keyring: Keyring) -> None:
    other = Keyring(kek=os.urandom(32), hmac_key=os.urandom(32))

    assert keyring.hash(OWNER) == keyring.hash(OWNER)
    assert keyring.hash(OWNER) != other.hash(OWNER)
    assert OWNER.encode() not in keyring.hash(OWNER)


def test_keyring_reads_base64_keys_from_settings() -> None:
    kek, hmac_key = os.urandom(32), os.urandom(32)
    settings = Settings(
        VIGIA_KEK=base64.b64encode(kek).decode(), PII_HMAC_KEY=base64.b64encode(hmac_key).decode()
    )

    assert Keyring.from_settings(settings).hash("x") == Keyring(kek, hmac_key).hash("x")


@pytest.mark.parametrize("kek", [None, base64.b64encode(b"short").decode()])
def test_keyring_refuses_missing_or_short_keys(kek: str | None) -> None:
    settings = Settings(VIGIA_KEK=kek, PII_HMAC_KEY=base64.b64encode(os.urandom(32)).decode())

    with pytest.raises(ValueError, match="VIGIA_KEK"):
        Keyring.from_settings(settings)


# --- Postgres: what a raw SELECT sees ----------------------------------------------


async def _enroll_everything(conn: asyncpg.Connection, recorder: Recorder) -> None:
    """Exercise every write path that stores PII: business, members, email, supplier, case."""
    ports = recorder.ports
    await handle(conn, ports, message(OWNER, "hola Ferreteria El Tornillo CO"))
    await handle(conn, ports, message(OWNER, f"equipo +57 300 000 0102 Ana Prueba {EMAIL}"))
    await handle(conn, ports, message(EMPLOYEE, f"codigo {_code(recorder)}"))
    supplier = "proveedor Aceros Prueba correo=pagos@aceros.example cuenta=0011223344"
    await handle(conn, ports, message(OWNER, supplier))
    await handle(conn, ports, message(EMPLOYEE, SECRET_TEXT))


def _code(recorder: Recorder) -> str:
    """The 6-digit code in the last email sent."""
    body = recorder.emails[-1][1]
    return next(
        word for word in body.replace(".", " ").split() if word.isdigit() and len(word) == 6
    )


_NEEDLES = (
    OWNER,
    OWNER.removeprefix("+"),
    "3000000102",
    EMAIL,
    "Ana Prueba",
    "Ferreteria El Tornillo",
    "pagos@aceros.example",
    "0011223344",
    "Aceros Prueba",
    SECRET_TEXT,
    "bancolombia-seguro",
)


async def _plaintext_hits(conn: asyncpg.Connection) -> list[str]:
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables"
        " WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
        " AND has_table_privilege(format('%I.%I', schemaname, tablename), 'SELECT')"
    )
    hits = []
    for row in tables:
        table = row["tablename"]
        for record in await conn.fetch(f'SELECT * FROM "{table}"'):  # noqa: S608 - catalog names
            for column, value in record.items():
                raw = value if isinstance(value, bytes) else str(value).encode()
                hits += [f"{table}.{column}: {n}" for n in _NEEDLES if n.encode() in raw]
    return hits


@pytest.mark.anyio
async def test_raw_select_as_superuser_finds_no_pii_in_any_table(
    admin_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_everything(admin_conn, recorder)

    assert await admin_conn.fetchval("SELECT count(*) FROM members") >= 2
    assert await admin_conn.fetchval("SELECT count(*) FROM cases") >= 1
    assert await _plaintext_hits(admin_conn) == []


@pytest.mark.anyio
async def test_raw_select_as_app_role_finds_no_pii(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_everything(app_conn, recorder)

    assert await app_conn.fetchval("SELECT count(*) FROM suppliers") == 1
    assert await _plaintext_hits(app_conn) == []


@pytest.mark.anyio
async def test_member_is_found_by_phone_hash_without_business_context(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await handle(app_conn, recorder.ports, message(OWNER, "hola Ferreteria El Tornillo CO"))
    await app_conn.execute("SELECT set_config('app.business_id', '', true)")

    member = await store.find_member(app_conn, recorder.keyring.hash(OWNER))

    assert member is not None
    assert member.role == "owner"
    assert await store.find_member(app_conn, recorder.keyring.hash("+573000000199")) is None


@pytest.mark.anyio
async def test_deleting_a_business_shreds_its_key(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_everything(app_conn, recorder)
    member = await store.find_member(app_conn, recorder.keyring.hash(OWNER))
    assert member is not None

    await store.delete_business(app_conn, member.business_id)

    assert await store.find_member(app_conn, recorder.keyring.hash(OWNER)) is None
    assert await app_conn.fetchval("SELECT count(*) FROM businesses") == 0
    with pytest.raises(LookupError):
        await store.open_business(app_conn, recorder.keyring, member.business_id)
