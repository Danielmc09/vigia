"""Enrollment over WhatsApp: commands, owner-only actions, email verification (specs/01)."""

import asyncpg
import pytest

from tests.conftest import Recorder, message
from vigia.config import settings
from vigia.i18n import t
from vigia.services.commands import parse_command
from vigia.services.ingest import handle
from vigia.store import postgres as store

OWNER = "+573000000201"
EMPLOYEE = "+573000000202"
STRANGER = "+573000000299"
US_OWNER = "+12025550101"
EMAIL = "luis.prueba@correo.example"


# --- Parsing (pure) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name", "args"),
    [
        ("hola", "enroll", ""),
        ("Hola Ferreteria El Tornillo CO", "enroll", "Ferreteria El Tornillo CO"),
        ("hello Corner Hardware US", "enroll", "Corner Hardware US"),
        ("equipo +57 300 000 0202 Luis", "team", "+57 300 000 0202 Luis"),
        ("mi correo luis@correo.example", "my_email", "luis@correo.example"),
        ("código 123456", "code", "123456"),
        ("proveedor Aceros correo=a@b.example", "supplier", "Aceros correo=a@b.example"),
        ("historial", "history", ""),
        ("AYUDA", "help", ""),
    ],
)
def test_commands_are_recognized_in_both_languages(text: str, name: str, args: str) -> None:
    command = parse_command(text)

    assert command is not None
    assert (command.name, command.args) == (name, args)


def test_command_carries_the_fields_of_its_grammar() -> None:
    command = parse_command("equipo +57 300 000 0202 Luis Perez luis@correo.example")

    assert command is not None
    assert command.fields == {
        "phone": "+57 300 000 0202",
        "name": "Luis Perez",
        "email": "luis@correo.example",
    }


@pytest.mark.parametrize(
    "text",
    [
        "equipo +57 300 000 0203 Luis",
        "equipo +573000000203 Ana Perez ana@correo.example",
        "equipo +573000000203 Luis@casa",  # name with "@"
        "equipo +573000000203 Ana a@b.example tarde",  # text after the email
        "proveedor Aceros correo=a@b.example cuenta=1",
        "mi correo ana@correo.example",
        "codigo 123456",
        "historial",
    ],
)
async def test_a_command_the_parser_accepts_never_falls_back_to_help(
    app_conn: asyncpg.Connection, recorder: Recorder, text: str
) -> None:
    await _enroll_owner(app_conn, recorder)
    command = parse_command(text)

    reply = await handle(app_conn, recorder.ports, message(OWNER, text))

    if command is not None:
        assert reply != t("es", "help")


@pytest.mark.parametrize(
    "text",
    [
        "Bancolombia: su cuenta sera bloqueada",
        "historial de pagos pendiente",  # no-argument command followed by text
        "codigo de verificacion 123",  # malformed code
        "equipo de soporte tecnico",  # team without a phone
        "mi correo es importante",
        "",
    ],
)
def test_non_commands_are_not_intercepted(text: str) -> None:
    assert parse_command(text) is None


# --- Flows against Postgres --------------------------------------------------------


async def _enroll_owner(conn: asyncpg.Connection, recorder: Recorder) -> str | None:
    return await handle(conn, recorder.ports, message(OWNER, "hola Ferreteria El Tornillo CO"))


def _code(recorder: Recorder) -> str:
    body = recorder.emails[-1][1]
    return next(
        word for word in body.replace(".", " ").split() if word.isdigit() and len(word) == 6
    )


pytestmark = pytest.mark.anyio


async def test_hello_without_details_explains_how_to_enroll(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    reply = await handle(app_conn, recorder.ports, message(STRANGER, "hola"))

    assert reply == t("es", "enroll.ask")
    assert await store.find_member(app_conn, recorder.keyring.hash(STRANGER)) is None


async def test_hello_with_name_and_country_creates_business_and_owner(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    reply = await _enroll_owner(app_conn, recorder)

    member = await store.find_member(app_conn, recorder.keyring.hash(OWNER))
    assert member is not None and member.role == "owner"
    assert reply == t("es", "enroll.done", business="Ferreteria El Tornillo")
    business, _ = await store.open_business(app_conn, recorder.keyring, member.business_id)
    assert (business.country, business.language) == ("CO", "es")


async def test_us_business_gets_english_replies(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    reply = await handle(app_conn, recorder.ports, message(US_OWNER, "hello Corner Hardware US"))

    assert reply == t("en", "enroll.done", business="Corner Hardware")


async def test_hello_with_unknown_country_asks_again(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    reply = await handle(app_conn, recorder.ports, message(STRANGER, "hola Tienda Prueba ZZ"))

    assert reply == t("es", "enroll.ask")
    assert await store.find_member(app_conn, recorder.keyring.hash(STRANGER)) is None


async def test_hello_from_enrolled_member_does_not_create_another_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "hola"))

    assert reply == t("es", "enroll.already")
    assert await app_conn.fetchval("SELECT count(*) FROM businesses") == 1


async def test_owner_adds_team_member_who_becomes_whitelisted(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "equipo +57 300 000 0202 Luis"))

    assert reply == t("es", "team.added", name="Luis")
    employee = await store.find_member(app_conn, recorder.keyring.hash(EMPLOYEE))
    assert employee is not None and employee.role == "employee"


@pytest.mark.edge("B5")
async def test_employee_cannot_add_team_members_or_suppliers(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "equipo +57 300 000 0202 Luis"))

    team = await handle(app_conn, recorder.ports, message(EMPLOYEE, "equipo +57 300 000 0203 Eva"))
    supplier = await handle(
        app_conn, recorder.ports, message(EMPLOYEE, "proveedor Aceros cuenta=0011223344")
    )

    assert team == supplier == t("es", "command.owner_only")
    assert await store.find_member(app_conn, recorder.keyring.hash("+573000000203")) is None


async def test_team_with_email_sends_a_code_to_that_email(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)

    reply = await handle(
        app_conn, recorder.ports, message(OWNER, f"equipo +57 300 000 0202 Luis {EMAIL}")
    )

    assert [to for to, _ in recorder.emails] == [EMAIL]
    assert reply == "\n".join(
        [t("es", "team.added", name="Luis"), t("es", "email.code_sent", email=EMAIL)]
    )


async def test_my_email_then_valid_code_links_the_email(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)

    sent = await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    verified = await handle(app_conn, recorder.ports, message(OWNER, f"codigo {_code(recorder)}"))

    assert sent == t("es", "email.code_sent", email=EMAIL)
    assert verified == t("es", "email.verified")
    by_email = await store.find_member(app_conn, recorder.keyring.hash(EMAIL))
    assert by_email is not None and by_email.role == "owner"


async def test_wrong_code_does_not_link_the_email(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    wrong = f"{(int(_code(recorder)) + 1) % 1_000_000:06d}"

    reply = await handle(app_conn, recorder.ports, message(OWNER, f"codigo {wrong}"))

    assert reply == t("es", "email.code_invalid")
    assert await store.find_member(app_conn, recorder.keyring.hash(EMAIL)) is None


@pytest.mark.edge("B4")
async def test_expired_code_does_not_link_the_email(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    await app_conn.execute("UPDATE verification_codes SET expires_at = now() - interval '1 second'")

    reply = await handle(app_conn, recorder.ports, message(OWNER, f"codigo {_code(recorder)}"))

    assert reply == t("es", "email.code_none")  # an expired code is no pending code (B3/B4)


async def test_owner_adds_supplier_to_the_address_book(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)

    reply = await handle(
        app_conn,
        recorder.ports,
        message(OWNER, "proveedor Aceros del Valle correo=pagos@aceros.example cuenta=0011223344"),
    )

    assert reply == t("es", "supplier.added", name="Aceros del Valle")
    assert await app_conn.fetchval("SELECT count(*) FROM suppliers") == 1


async def test_history_is_empty_for_a_new_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "historial"))

    assert reply == t("es", "history.empty")


async def test_history_lists_the_last_five_verdicts_newest_first(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    for day, label in enumerate(["legit", "scam", "scam", "doubtful", "legit", "scam"], start=1):
        await app_conn.execute(
            "INSERT INTO verdicts (business_id, label, created_at)"
            " SELECT id, $1, make_timestamptz(2026, 10, $2, 12, 0, 0) FROM businesses",
            label,
            day,
        )

    reply = await handle(app_conn, recorder.ports, message(OWNER, "historial"))

    assert reply is not None
    lines = reply.splitlines()
    assert lines[0] == t("es", "history.header")
    assert lines[1] == t(
        "es", "history.line", date="2026-10-06", label=t("es", "verdict.label.scam")
    )
    assert len(lines) == 6


async def test_help_lists_the_commands(app_conn: asyncpg.Connection, recorder: Recorder) -> None:
    await _enroll_owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "ayuda"))

    assert reply == t("es", "help")


async def test_email_enrolled_in_any_case_is_found_from_the_email_channel(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "mi correo Luis.Prueba@Correo.EXAMPLE"))
    await handle(app_conn, recorder.ports, message(OWNER, f"codigo {_code(recorder)}"))

    forwarded = message(
        "  luis.prueba@correo.example ", "Pague esta factura", origin="email", kind="email"
    )
    reply = await handle(app_conn, recorder.ports, forwarded)

    assert reply is None
    assert len(recorder.analyzed) == 1


async def test_adv_relay_correo_team_cannot_mail_one_address_without_limit(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Incident adv-relay-correo: new members per call must not reset the cap (D-028)."""
    victim = "victima@correo.example"
    await _enroll_owner(app_conn, recorder)

    replies = [
        await handle(app_conn, recorder.ports, message(OWNER, f"equipo +57300000{n} A {victim}"))
        for n in ("0301", "0302", "0303", "0304")
    ]

    assert len(recorder.emails) == settings.email_codes_per_hour
    assert replies[-1] == "\n".join(
        [t("es", "team.added", name="A"), t("es", "email.code_rate_limited")]
    )
    added = await store.find_member(app_conn, recorder.keyring.hash("+573000000304"))
    assert added is not None


async def test_email_codes_to_one_address_are_capped_across_businesses(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    for _ in range(settings.email_codes_per_hour):
        await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    await handle(app_conn, recorder.ports, message(US_OWNER, "hello Corner Hardware US"))

    reply = await handle(app_conn, recorder.ports, message(US_OWNER, f"my email {EMAIL}"))

    assert reply == t("en", "email.code_rate_limited")
    assert len(recorder.emails) == settings.email_codes_per_hour


async def test_email_codes_per_business_are_capped_per_hour(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    for i in range(settings.email_codes_per_business_hour):
        await handle(app_conn, recorder.ports, message(OWNER, f"mi correo v{i}@correo.example"))

    reply = await handle(app_conn, recorder.ports, message(OWNER, "mi correo otra@correo.example"))

    assert reply == t("es", "email.code_rate_limited")
    assert len(recorder.emails) == settings.email_codes_per_business_hour


async def test_code_is_invalidated_after_too_many_wrong_attempts(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    right = _code(recorder)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    for _ in range(settings.code_max_attempts):
        await handle(app_conn, recorder.ports, message(OWNER, f"codigo {wrong}"))

    reply = await handle(app_conn, recorder.ports, message(OWNER, f"codigo {right}"))

    assert reply == t("es", "email.code_locked")
    assert await store.find_member(app_conn, recorder.keyring.hash(EMAIL)) is None
