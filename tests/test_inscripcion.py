"""Enrollment over WhatsApp: commands, owner-only actions, email verification (specs/01)."""

import asyncpg
import pytest

from tests.conftest import Recorder, message
from tests.conftest import escalation_id as _escalation_id
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
        "transferir +57 300 000 0203",
        "confirmar 0A1B2C3D",
        "cancelar 0a1b2c3d",
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
        "confirmar compra",  # a forwarded scam asking to confirm, not an escalation id
        "Cancelar suscripcion",
        "transferir ya",
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

    assert reply == t("es", "team.request_received", name="Luis")
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
        [t("es", "team.request_received", name="Luis"), t("es", "email.code_sent", email=EMAIL)]
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
        [t("es", "team.request_received", name="A"), t("es", "email.code_rate_limited")]
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


async def _second_business(conn: asyncpg.Connection, recorder: Recorder) -> None:
    await handle(conn, recorder.ports, message(STRANGER, "hola Panaderia La Espiga CO"))


async def test_adv_enumeracion_contacto_phone_of_another_business_looks_like_a_new_one(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Incident adv-enumeracion-contacto: ``equipo`` must not reveal who is enrolled elsewhere."""
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "equipo +57 300 000 0202 Luis"))
    await _second_business(app_conn, recorder)

    taken = await handle(
        app_conn, recorder.ports, message(STRANGER, "equipo +57 300 000 0202 Luis")
    )
    free = await handle(app_conn, recorder.ports, message(STRANGER, "equipo +57 300 000 0298 Luis"))

    assert taken == free == t("es", "team.request_received", name="Luis")
    employee = await store.find_member(app_conn, recorder.keyring.hash(EMPLOYEE))
    owner = await store.find_member(app_conn, recorder.keyring.hash(OWNER))
    assert employee is not None and owner is not None
    assert employee.business_id == owner.business_id


async def test_adv_enumeracion_contacto_email_of_another_business_looks_like_a_new_one(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Incident adv-enumeracion-contacto: a verified email elsewhere gets no tell either."""
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    await handle(app_conn, recorder.ports, message(OWNER, f"codigo {_code(recorder)}"))
    await _second_business(app_conn, recorder)
    sent_before = len(recorder.emails)

    taken = await handle(app_conn, recorder.ports, message(STRANGER, f"mi correo {EMAIL}"))
    free = await handle(
        app_conn, recorder.ports, message(STRANGER, "mi correo libre@correo.example")
    )

    assert taken == t("es", "email.code_sent", email=EMAIL)
    assert free == t("es", "email.code_sent", email="libre@correo.example")
    assert [to for to, _ in recorder.emails[sent_before:]] == ["libre@correo.example"]


async def test_adv_enumeracion_reintento_second_team_command_does_not_reveal_another_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Incident adv-enumeracion-reintento: sending ``equipo`` twice tells free from taken."""
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "equipo +57 300 000 0202 Luis"))
    await _second_business(app_conn, recorder)
    replies = []
    for phone in ("+57 300 000 0202", "+57 300 000 0298"):  # elsewhere, then free
        for _ in range(2):
            text = f"equipo {phone} Luis"
            replies.append(await handle(app_conn, recorder.ports, message(STRANGER, text)))

    assert set(replies) == {t("es", "team.request_received", name="Luis")}
    free = await store.find_member(app_conn, recorder.keyring.hash("+573000000298"))
    assert free is not None and free.role == "employee"


async def test_adv_enumeracion_transferir_transfer_does_not_reveal_another_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Incident adv-enumeracion-transferir: ``equipo`` + ``transferir`` tells free from taken."""
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "equipo +57 300 000 0202 Luis"))
    await _second_business(app_conn, recorder)
    asks = {}
    for phone in (EMPLOYEE, "+573000000298"):  # enrolled in the first business, then free
        await handle(app_conn, recorder.ports, message(STRANGER, f"equipo {phone} Luis"))
        asks[phone] = await handle(
            app_conn, recorder.ports, message(STRANGER, f"transferir {phone}")
        )

    for phone, ask in asks.items():
        assert ask == t("es", "escalation.transfer_requested", id=_escalation_id(ask), target=phone)
    confirm = f"confirmar {_escalation_id(asks[EMPLOYEE])}"
    reply = await handle(app_conn, recorder.ports, message(STRANGER, confirm, origin="wazenda"))
    assert reply == t("es", "escalation.unknown")  # nothing was raised for the other business
    employee = await store.find_member(app_conn, recorder.keyring.hash(EMPLOYEE))
    assert employee is not None and employee.role == "employee"


async def test_adv_enumeracion_tope_correo_cap_does_not_reveal_another_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Incident adv-enumeracion-tope-correo: the per-address cap tells free from taken.

    A code is never issued to an email verified elsewhere, so its counter never moves and
    it never hits ``email.code_rate_limited``, while a free address does (juez T-08).
    """
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    await handle(app_conn, recorder.ports, message(OWNER, f"codigo {_code(recorder)}"))
    await _second_business(app_conn, recorder)
    tries = settings.email_codes_per_hour + 1
    replies = {}
    for email in (EMAIL, "libre@correo.example"):  # verified elsewhere, then free
        text = f"mi correo {email}"
        replies[email] = [
            await handle(app_conn, recorder.ports, message(STRANGER, text)) for _ in range(tries)
        ]

    assert replies[EMAIL][-1] == t("es", "email.code_rate_limited")
    assert replies["libre@correo.example"][-1] == t("es", "email.code_rate_limited")


@pytest.mark.parametrize("email", [EMAIL, "libre@correo.example"], ids=["taken", "free"])
async def test_adv_enumeracion_codigo_senuelo_code_does_not_reveal_another_business(
    app_conn: asyncpg.Connection, recorder: Recorder, email: str
) -> None:
    """Incident adv-enumeracion-codigo-senuelo: ``codigo`` after ``mi correo`` tells them apart.

    The decoy for an email verified elsewhere was born expired (``email.code_none``) while a
    free address has a live code the prober mistypes (``email.code_invalid``, then locked).
    """
    await _enroll_owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, f"mi correo {EMAIL}"))
    await handle(app_conn, recorder.ports, message(OWNER, f"codigo {_code(recorder)}"))
    await _second_business(app_conn, recorder)
    tries = settings.code_max_attempts + 1

    await handle(app_conn, recorder.ports, message(STRANGER, f"mi correo {email}"))
    replies = [
        await handle(app_conn, recorder.ports, message(STRANGER, "codigo 000000"))
        for _ in range(tries)
    ]

    invalid, locked = t("es", "email.code_invalid"), t("es", "email.code_locked")
    assert replies == [invalid] * (tries - 2) + [locked] * 2


@pytest.mark.parametrize("phone", [OWNER, "+573000000298"], ids=["taken", "free"])
async def test_adv_enumeracion_codigo_senuelo_team_email_leaves_the_owner_without_a_code(
    app_conn: asyncpg.Connection, recorder: Recorder, phone: str
) -> None:
    """``equipo`` with a phone of another business files the decoy under the sender.

    For a free phone the code belongs to the new employee, so the sender has none; the
    decoy must not give the sender a live code either.
    """
    await _enroll_owner(app_conn, recorder)
    await _second_business(app_conn, recorder)

    await handle(
        app_conn, recorder.ports, message(STRANGER, f"equipo {phone} Luis libre@correo.example")
    )
    reply = await handle(app_conn, recorder.ports, message(STRANGER, "codigo 000000"))

    assert reply == t("es", "email.code_none")


# --- Incident inc-equipo-sin-respuesta (T-10b, 30 oct) -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "equipo +573000000703 Luis",
        "equipo 573000000703 Luis",  # no "+"
        "equipo ‪+57 300 000 0703‬ Luis",  # bidi marks WhatsApp adds on copy
        "equipo +57 300 000 0703 Luis",  # non-breaking spaces
        "Equipo +57 (300) 000-0703 Luis",
    ],
)
async def test_inc_equipo_sin_respuesta_any_phone_shape_enrolls_and_answers(
    app_conn: asyncpg.Connection, recorder: Recorder, text: str
) -> None:
    """Incident inc-equipo-sin-respuesta: the owner's ``equipo`` got no reply, no member."""
    await _enroll_owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, text))

    assert reply == t("es", "team.request_received", name="Luis")
    assert await store.find_member(app_conn, recorder.keyring.hash("+573000000703")) is not None
    assert recorder.analyzed == []


@pytest.mark.parametrize(
    ("text", "usage"),
    [
        ("equipo Luis", "usage.team"),
        ("equipo +57 321 Luis", "usage.team"),
        ("equipo 3000000703 Luis", "usage.team"),  # no country code: would enroll +32...
        ("proveedor Aceros", "usage.supplier"),
        ("confirmar 14x", "usage.confirm"),
        ("transferir a Luis", "usage.transfer"),
        ("estafa confirmada ayer", "usage.scam_confirmed"),
    ],
)
async def test_inc_equipo_sin_respuesta_a_misused_command_gets_its_usage_not_a_case(
    app_conn: asyncpg.Connection, recorder: Recorder, text: str, usage: str
) -> None:
    """Incident inc-equipo-sin-respuesta: a reserved word with a bad format fell to a case."""
    await _enroll_owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, text))

    assert reply == t("es", usage)
    assert recorder.analyzed == []
    assert await app_conn.fetchval("SELECT count(*) FROM members") == 1


async def test_a_long_forward_that_opens_with_a_reserved_word_is_still_a_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _enroll_owner(app_conn, recorder)
    text = "Equipo de seguridad Bancolombia: su cuenta fue bloqueada, valide sus datos hoy mismo"

    reply = await handle(app_conn, recorder.ports, message(OWNER, text))

    assert reply is None
    assert len(recorder.analyzed) == 1
