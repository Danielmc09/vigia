"""Edge cases of specs/12 marked T: one test per row, xfail while its piece does not exist.

Each test names its row with ``@pytest.mark.edge`` (D-036); ``tools/validate_edges.py``
lists the T rows that no test names.
"""

import asyncio
import importlib
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import Recorder, message
from tests.conftest import escalation_id as _escalation_id
from vigia.app import create_app
from vigia.config import Settings
from vigia.domain.attachment import MAX_ATTACHMENT_BYTES, Attachment, check_image, read_capped
from vigia.domain.escalation import EXPIRY, Escalation
from vigia.domain.member import Business
from vigia.domain.rejection import Rejection
from vigia.domain.urls import extract_urls
from vigia.i18n import t
from vigia.jev.gate import screen
from vigia.model.jev import JevJudge, make_judge
from vigia.policy.actions import confirm, new_escalation
from vigia.services.ingest import handle, normalize
from vigia.signals.country_pack import country_pack
from vigia.signals.detector import detect
from vigia.signals.patterns import LONG_TEXT_CHARS, pattern_signals
from vigia.store import postgres as store
from vigia.store.migrate import MIGRATIONS_DIR, apply_migrations

OWNER = "+573000000501"
EMPLOYEE = "+573000000502"
CO = country_pack("CO")
pytestmark = pytest.mark.anyio


def _xfail(task: str, raises: type[BaseException] | None = None) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=task, raises=raises)


async def _owner(conn: asyncpg.Connection, recorder: Recorder) -> None:
    await handle(conn, recorder.ports, message(OWNER, "hola Ferreteria El Tornillo CO"))


async def _team(conn: asyncpg.Connection, recorder: Recorder) -> None:
    await _owner(conn, recorder)
    await handle(conn, recorder.ports, message(OWNER, "equipo +57 300 000 0502 Luis"))


def _case_from(text: str):  # noqa: ANN202 - Case
    msg = message(OWNER, text)
    case = normalize(msg, Business(uuid4(), "CO", "es"))
    assert case is not None
    return case


# --- A. Input (pure) ---------------------------------------------------------------


@pytest.mark.edge("A2")
def test_a2_text_over_the_limit_adds_a_light_signal() -> None:
    assert CO is not None
    fired = pattern_signals("a " * (LONG_TEXT_CHARS // 2 + 1), CO, False)
    at_limit = pattern_signals("a" * LONG_TEXT_CHARS, CO, False)

    assert [(s.type, s.weight) for s in fired] == [("text_too_long", "light")]
    assert at_limit == []


@pytest.mark.edge("A5")
@pytest.mark.parametrize("data", [b"", b"not an image at all", b"\xff\xd8", b"GIF8"])
def test_a5_empty_or_corrupt_image_is_a_rejection(data: bytes) -> None:
    rejection = check_image(data)

    assert isinstance(rejection, Rejection)
    assert rejection.error_code == "INVALID_ATTACHMENT"


@pytest.mark.edge("A5")
@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_a5_readable_image_formats_pass(fmt: str) -> None:
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, fmt)
    assert check_image(out.getvalue()) is None


async def _chunks(count: int, size: int, pulled: list[int]) -> AsyncIterator[bytes]:
    for _ in range(count):
        pulled.append(size)
        yield b"x" * size


@pytest.mark.edge("A6")
async def test_a6_attachment_over_10_mb_is_rejected_without_reading_the_rest() -> None:
    pulled: list[int] = []
    mb = 1024 * 1024

    result = await read_capped(_chunks(100, mb, pulled))

    assert MAX_ATTACHMENT_BYTES == 10 * mb
    assert isinstance(result, Rejection) and result.error_code == "TOO_LARGE"
    assert len(pulled) == 11  # stops at the first chunk past the cap


@pytest.mark.edge("A6")
async def test_a6_attachment_under_the_cap_is_read_whole() -> None:
    assert await read_capped(_chunks(3, 4, []), limit=12) == b"x" * 12


@pytest.mark.edge("A19")
def test_a19_zero_width_and_bidi_controls_are_removed_before_urls() -> None:
    case = _case_from("Ingrese a banco​lombia-se⁠guro.co/v ‮ahora")

    assert case.urls == ["bancolombia-seguro.co/v"]
    assert case.text == "Ingrese a bancolombia-seguro.co/v ahora"


@pytest.mark.edge("A19")
@pytest.mark.parametrize(
    "text",
    [
        "señor.Está listo",
        "Don Ramón.Mañana",
        "Llegó el camión.Bancolombia confirmó el pago",
        "Llegó el pago.Bancolombia confirmó la transferencia",  # c-0052
        "Listo el envio.Servientrega lo recoge mañana",
        "The money is in.Chase confirmed it",  # c-0053
    ],
)
def test_a19_incidents_c0051_to_c0053_prose_without_space_after_a_period_is_not_a_url(
    text: str,
) -> None:
    assert extract_urls(text) == []


@pytest.mark.edge("A19")
def test_a19_fullwidth_text_is_folded_to_ascii_before_patterns() -> None:
    assert CO is not None
    case = _case_from("Ｅｎｖíe su ｃｌａｖｅ a https://bancolombia-seguro.co/v")

    assert "clave" in (case.text or "")
    assert "credentials_request" in {s.type for s in detect(case, CO)}


@pytest.mark.edge("A19")
def test_a19_cyrillic_homograph_domain_is_a_hard_lookalike() -> None:
    assert CO is not None
    case = _case_from("Ingrese a https://b\u0430ncolombia.com/login ya")

    assert case.urls == ["https://b\u0430ncolombia.com/login"]
    hard = {s.type for s in detect(case, CO) if s.weight == "hard"}
    assert "lookalike_domain" in hard


@pytest.mark.edge("A19")
@pytest.mark.parametrize(
    "host",
    [
        "chase.com.secure-login.ru",
        "dian.gov.co.pagos.pw",
        "bancolombia.com.pagos.zip",
        "a.com.b.zz9",
        "https://chase.com.x9y/login",
    ],
)
def test_a19_extract_urls_never_returns_the_prefix_of_a_longer_host(host: str) -> None:
    assert extract_urls(f"verifique en {host} hoy") in ([], [host])


@pytest.mark.edge("A19")
@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("DIAN: tiene una multa pendiente, pague en dian.gov.co.pagos.pw", "dian.gov.co.pagos.pw"),
        (
            "Chase: your account is locked, verify at chase.com.secure-login.ru",
            "chase.com.secure-login.ru",
        ),
        (
            "Bancolombia: su cuenta fue bloqueada. "
            "Ingrese su clave en bancolombia-verificar.ru/login",
            "bancolombia-verificar.ru/login",
        ),
    ],
)
def test_a19_incidents_c0054_to_c0056_whole_host_with_any_cctld_is_extracted(
    text: str, url: str
) -> None:
    assert extract_urls(text) == [url]


@pytest.mark.edge("A19")
@pytest.mark.parametrize("text", ["pago.Me avisas", "Hola.Co", "Llegó el pago.Bancolombia"])
def test_a19_sentence_case_last_label_without_scheme_is_prose(text: str) -> None:
    assert extract_urls(text) == []


@pytest.mark.edge("A19")
def test_a19_accented_homograph_with_scheme_is_a_hard_lookalike() -> None:
    assert CO is not None
    case = _case_from("Ingrese a https://bancolómbia.com/login ya")

    assert case.urls == ["https://bancolómbia.com/login"]
    hard = {s.type for s in detect(case, CO) if s.weight == "hard"}
    assert "lookalike_domain" in hard


def test_host_ends_before_an_underscore() -> None:
    assert extract_urls("Pague en www.falso.com_pago hoy") == ["www.falso.com"]


def test_scheme_less_host_needs_a_known_tld_but_a_scheme_takes_any() -> None:
    assert extract_urls("Reclama en bit.ly/3xQnq y bancolombia-seguro.co/v") == [
        "bit.ly/3xQnq",
        "bancolombia-seguro.co/v",
    ]
    assert extract_urls("Entra a https://pago.bancolombia/x") == ["https://pago.bancolombia/x"]


# --- A. Input (ingestion against Postgres) -----------------------------------------


@pytest.mark.edge("A1")
@pytest.mark.parametrize("text", ["", "   ", "\U0001f44d", "?", "\U0001f64f\U0001f3fb ?!"])
async def test_a1_empty_or_symbol_only_message_gets_short_help_and_no_case(
    app_conn: asyncpg.Connection, recorder: Recorder, text: str
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, text))

    assert reply == t("es", "case.empty")
    assert recorder.analyzed == []
    assert await app_conn.fetchval("SELECT count(*) FROM cases") == 0


@pytest.mark.edge("A2")
async def test_a2_long_text_is_still_analyzed_whole(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    text = "Su paquete esta retenido en bodega. " * 150

    await handle(app_conn, recorder.ports, message(OWNER, text))

    [case] = recorder.analyzed
    assert case.text == text and len(text) > LONG_TEXT_CHARS


@pytest.mark.edge("A3")
async def test_a3_sticker_location_contact_poll_or_reaction_is_declined(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, None, kind="unknown"))

    assert reply == t("es", "case.unsupported")
    assert recorder.analyzed == []


@pytest.mark.edge("A4")
async def test_a4_several_images_are_attachments_of_one_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    images = [Attachment(ref=f"img-{i}", mime="image/jpeg") for i in range(3)]

    await handle(app_conn, recorder.ports, message(OWNER, "mira", kind="image", attachments=images))

    [case] = recorder.analyzed
    assert case.kind == "image"
    assert [a.ref for a in case.attachments] == ["img-0", "img-1", "img-2"]


@pytest.mark.edge("A25")
@_xfail("T-10: dedupe by content fingerprint and cached verdict")
async def test_a25_same_message_from_three_members_is_analyzed_once(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _team(app_conn, recorder)
    text = "Su paquete esta retenido, pague en servientrega-guia.co"

    for sender in (OWNER, EMPLOYEE, OWNER):
        await handle(app_conn, recorder.ports, message(sender, text))

    assert len(recorder.analyzed) == 1


@pytest.mark.edge("A26")
async def test_a26_owner_forwarding_a_test_message_is_analyzed_normally(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(
        app_conn, recorder.ports, message(OWNER, "Prueba: su cuenta fue bloqueada")
    )

    assert reply is None
    assert [c.text for c in recorder.analyzed] == ["Prueba: su cuenta fue bloqueada"]


def _eml(sender: str, body: str) -> bytes:
    mail = EmailMessage()
    mail["From"] = sender
    mail["Subject"] = "Factura"
    mail.set_content(body)
    return mail.as_bytes()


@pytest.mark.edge("A16")
async def test_a16_nested_eml_is_processed_only_at_the_first_level(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    outer = EmailMessage()
    outer["From"] = "proveedor@aceros.example"
    outer.set_content("Ver adjunto")
    outer.add_attachment(_eml("otro@x.example", "Pague a la cuenta 99887766"), "message", "rfc822")

    await handle(
        app_conn, recorder.ports, message(OWNER, None, kind="email", eml_bytes=outer.as_bytes())
    )

    [case] = recorder.analyzed
    assert case.text == "Ver adjunto"
    assert case.email is not None and case.email.sender == "proveedor@aceros.example"


# --- B. Channel and enrollment -------------------------------------------------------


@pytest.mark.edge("B1", "B2")
@_xfail("T-10: Wazenda webhook (signature format pending T-01 fixtures)")
def test_b1_b2_webhook_rejects_an_unsigned_request_with_401() -> None:
    client = TestClient(create_app(Settings()))
    body = {"message_id": "m-1", "from": OWNER, "text": "hola"}

    unsigned = client.post("/webhook/wazenda", json=body)
    assert unsigned.status_code == 401


@pytest.mark.edge("B3")
async def test_b3_code_without_a_pending_one_says_there_is_none(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "codigo 123456"))

    assert reply == t("es", "email.code_none")


@pytest.mark.edge("B4")
async def test_b4_three_wrong_codes_invalidate_the_live_code(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "mi correo luis@correo.example"))
    [(_, body)] = recorder.emails
    right = next(w for w in body.replace(".", " ").split() if w.isdigit() and len(w) == 6)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    for _ in range(3):
        await handle(app_conn, recorder.ports, message(OWNER, f"codigo {wrong}"))

    reply = await handle(app_conn, recorder.ports, message(OWNER, f"codigo {right}"))

    assert reply == t("es", "email.code_locked")


@pytest.mark.edge("B5")
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("apikey", marks=_xfail("T-17c: apikey command")),
        pytest.param("panel", marks=_xfail("T-17b: panel command")),
    ],
)
async def test_b5_employee_cannot_ask_for_apikey_or_panel(
    app_conn: asyncpg.Connection, recorder: Recorder, text: str
) -> None:
    await _team(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(EMPLOYEE, text))

    assert reply == t("es", "command.owner_only")


@pytest.mark.edge("B6")
async def test_b6_number_of_another_business_cannot_join_a_second_one(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """Not added, and the reply is the neutral one of any new member (adv-enumeracion-contacto)."""
    await _team(app_conn, recorder)
    first = await store.find_member(app_conn, recorder.keyring.hash(OWNER))
    other = "+573000000599"
    await handle(app_conn, recorder.ports, message(other, "hola Panaderia La Espiga CO"))

    reply = await handle(app_conn, recorder.ports, message(other, "equipo +57 300 000 0502 Luis"))

    assert reply == t("es", "team.request_received", name="Luis")
    employee = await store.find_member(app_conn, recorder.keyring.hash(EMPLOYEE))
    assert first is not None and employee is not None
    assert employee.business_id == first.business_id


@pytest.mark.edge("B12")
async def test_b12_us_business_gets_english_replies_even_to_spanish_commands(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    enrolled = await handle(app_conn, recorder.ports, message(OWNER, "hola Corner Hardware US"))

    reply = await handle(app_conn, recorder.ports, message(OWNER, "ayuda"))

    assert enrolled == t("en", "enroll.done", business="Corner Hardware")
    assert reply == t("en", "help")


async def _role(conn: asyncpg.Connection, recorder: Recorder, phone: str) -> str | None:
    member = await store.find_member(conn, recorder.keyring.hash(phone))
    return member.role if member else None


@pytest.mark.edge("B7")
async def test_b7_owner_transfers_the_business(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _team(app_conn, recorder)
    ask = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0502"))

    assert await _role(app_conn, recorder, EMPLOYEE) == "employee"  # nothing runs unconfirmed
    confirm = f"confirmar {_escalation_id(ask)}"
    done = await handle(app_conn, recorder.ports, message(OWNER, confirm, origin="wazenda"))

    assert done == t("es", "escalation.transfer_confirmed", target=EMPLOYEE)
    assert await _role(app_conn, recorder, EMPLOYEE) == "owner"
    assert await _role(app_conn, recorder, OWNER) == "employee"


@pytest.mark.edge("B7")
async def test_b7_confirm_from_the_local_channel_never_transfers(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _team(app_conn, recorder)
    ask = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0502"))

    await handle(app_conn, recorder.ports, message(OWNER, f"confirmar {_escalation_id(ask)}"))

    assert await _role(app_conn, recorder, EMPLOYEE) == "employee"


@pytest.mark.edge("B7")
async def test_b7_only_the_owner_transfers_and_confirms(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _team(app_conn, recorder)
    by_employee = await handle(
        app_conn, recorder.ports, message(EMPLOYEE, "transferir +57 300 000 0501")
    )
    ask = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0502"))
    confirm = f"confirmar {_escalation_id(ask)}"
    stolen = await handle(app_conn, recorder.ports, message(EMPLOYEE, confirm, origin="wazenda"))

    assert by_employee == t("es", "command.owner_only")
    assert stolen == t("es", "command.owner_only")
    assert await _role(app_conn, recorder, EMPLOYEE) == "employee"


@pytest.mark.edge("B7")
async def test_b7_transfer_only_to_a_member_of_the_team(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """A non-member gets the reply of a valid request, but nothing to confirm (D-055)."""
    await _team(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0999"))
    confirm = message(OWNER, f"confirmar {_escalation_id(reply)}", origin="wazenda")

    target = "+573000000999"
    assert reply == t(
        "es", "escalation.transfer_requested", id=_escalation_id(reply), target=target
    )
    assert await handle(app_conn, recorder.ports, confirm) == t("es", "escalation.unknown")
    assert await _role(app_conn, recorder, OWNER) == "owner"


@pytest.mark.edge("B7")
async def test_b7_cancelled_or_unknown_escalation_does_nothing(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _team(app_conn, recorder)
    ask = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0502"))
    escalation_id = _escalation_id(ask)

    cancelled = await handle(app_conn, recorder.ports, message(OWNER, f"cancelar {escalation_id}"))
    confirm = message(OWNER, f"confirmar {escalation_id}", origin="wazenda")
    late = await handle(app_conn, recorder.ports, confirm)
    unknown = await handle(app_conn, recorder.ports, message(OWNER, "confirmar 0000ABCD"))

    assert cancelled == t("es", "escalation.cancelled")
    assert late == t("es", "escalation.not_pending")
    assert unknown == t("es", "escalation.unknown")
    assert await _role(app_conn, recorder, EMPLOYEE) == "employee"


@pytest.mark.edge("B7")
async def test_b7_an_owner_cannot_confirm_another_business_escalation(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _team(app_conn, recorder)
    ask = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0502"))
    other = "+573000000599"
    await handle(app_conn, recorder.ports, message(other, "hola Panaderia La Espiga CO"))

    confirm = message(other, f"confirmar {_escalation_id(ask)}", origin="wazenda")
    reply = await handle(app_conn, recorder.ports, confirm)

    assert reply == t("es", "escalation.unknown")
    assert await _role(app_conn, recorder, EMPLOYEE) == "employee"
    assert await _role(app_conn, recorder, OWNER) == "owner"


@pytest.mark.edge("B7")
@pytest.mark.parametrize("verb", ["confirmar", "cancelar"])
async def test_b7_an_employee_cannot_tell_a_real_escalation_id_from_a_made_up_one(
    app_conn: asyncpg.Connection, recorder: Recorder, verb: str
) -> None:
    await _team(app_conn, recorder)
    ask = await handle(app_conn, recorder.ports, message(OWNER, "transferir +57 300 000 0502"))

    real = await handle(
        app_conn, recorder.ports, message(EMPLOYEE, f"{verb} {_escalation_id(ask)}")
    )
    made_up = await handle(app_conn, recorder.ports, message(EMPLOYEE, f"{verb} 0000ABCD"))

    assert real == made_up == t("es", "command.owner_only")


@pytest.mark.edge("D7")
def test_d7_confirming_twice_does_not_run_it_again() -> None:
    now = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    once = confirm(new_escalation("report", "fraude@example.com", uuid4(), now), True, now)
    assert isinstance(once, Escalation)

    twice = confirm(once, True, now)

    assert isinstance(twice, Rejection)
    assert twice.error_code == "NOT_PENDING"


@pytest.mark.edge("D8")
def test_d8_an_escalation_expires_24_hours_after_it_was_raised() -> None:
    raised = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    pending = new_escalation("report", "fraude@example.com", uuid4(), raised)

    in_time = confirm(pending, True, raised + EXPIRY - timedelta(seconds=1))
    late = confirm(pending, True, raised + EXPIRY)

    assert isinstance(in_time, Escalation)
    assert isinstance(late, Rejection)
    assert late.error_code == "EXPIRED"


@pytest.mark.edge("B8", "B9")
@pytest.mark.parametrize("flag", ["is_group", "edited"])
@_xfail("T-10: the channel adapter flags group and edited messages", AssertionError)
async def test_b8_b9_group_or_edited_messages_are_ignored(
    app_conn: asyncpg.Connection, recorder: Recorder, flag: str
) -> None:
    await _owner(app_conn, recorder)

    await handle(
        app_conn, recorder.ports, message(OWNER, "Su cuenta fue bloqueada", **{flag: True})
    )

    assert recorder.analyzed == []


@pytest.mark.edge("B10")
async def test_b10_eml_of_an_enrolled_member_from_an_unverified_address_is_ignored(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    eml = _eml("luis@correo.example", f"Reenvio de {OWNER}")

    reply = await handle(
        app_conn,
        recorder.ports,
        message("extrano@correo.example", None, origin="email", kind="email", eml_bytes=eml),
    )

    assert reply is None and recorder.analyzed == []


@pytest.mark.edge("B11")
@_xfail("T-17: email channel splits a forward with several .eml into one case each", AssertionError)
async def test_b11_forward_with_three_eml_gives_three_cases(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    emls = [_eml(f"p{i}@x.example", f"Factura {i}") for i in range(3)]

    await handle(app_conn, recorder.ports, message(OWNER, None, kind="email", emls=emls))

    assert len(recorder.analyzed) == 3


# --- C. Models and dependencies ------------------------------------------------------


@pytest.mark.edge("C10")
def test_c10_jev_down_or_without_key_falls_back_to_mock_investigate() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    keyed = JevJudge("k", client=httpx.Client(transport=httpx.MockTransport(down)))
    keyless = make_judge(Settings(JEV_BACKEND="real", JEV_API_KEY=None))
    assert CO is not None

    results = [screen("Su paquete esta retenido", [], CO, j) for j in (keyed, keyless)]

    assert all(
        (r.manipulation_detected, r.triage, r.source) == (False, "investigate", "mock")
        for r in results
    )


@pytest.mark.edge("C12")
@_xfail("T-13b: /salud checks its dependencies")
def test_c12_health_fails_when_postgres_is_down() -> None:
    down = Settings(database_url="postgresql://vigia_app:x@127.0.0.1:1/vigia")

    assert TestClient(create_app(down)).get("/salud").status_code == 503


# --- D. Time, limits and data --------------------------------------------------------


@pytest.mark.edge("D4")
async def test_d4_two_cases_of_one_business_at_once_do_not_block(
    admin_dsn: str, recorder: Recorder
) -> None:
    dsn = Settings().database_url
    conns = [await asyncpg.connect(dsn) for _ in range(3)]
    try:
        async with conns[0].transaction():  # committed: both workers must see the business
            await _owner(conns[0], recorder)

        async def forward(conn: asyncpg.Connection, text: str) -> str | None:
            async with conn.transaction():
                return await handle(conn, recorder.ports, message(OWNER, text))

        replies = await asyncio.wait_for(
            asyncio.gather(forward(conns[1], "Mensaje uno"), forward(conns[2], "Mensaje dos")), 5
        )

        assert replies == [None, None]
        assert sorted(c.text or "" for c in recorder.analyzed) == ["Mensaje dos", "Mensaje uno"]
    finally:
        member = await store.find_member(conns[0], recorder.keyring.hash(OWNER))
        admin = await asyncpg.connect(admin_dsn)
        if member is not None:
            await admin.execute("DELETE FROM businesses WHERE id = $1", member.business_id)
        await admin.close()
        for conn in conns:
            await conn.close()


@pytest.mark.edge("D11")
@pytest.mark.xfail(strict=True, raises=TypeError, reason="T-19: runner takes a migrations dir")
async def test_d11_a_new_migration_over_a_database_with_data_keeps_every_row(
    admin_dsn: str, tmp_path: Path
) -> None:
    for path in MIGRATIONS_DIR.glob("*.sql"):
        (tmp_path / path.name).write_text(path.read_text("utf-8"), "utf-8")
    (tmp_path / "9999_probe.sql").write_text("ALTER TABLE businesses ADD COLUMN probe text;")
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute("DROP DATABASE IF EXISTS vigia_d11")
    await admin.execute("CREATE DATABASE vigia_d11")
    scratch = admin_dsn.rsplit("/", 1)[0] + "/vigia_d11"
    try:
        await apply_migrations(scratch)
        conn = await asyncpg.connect(scratch)
        await conn.execute(
            "INSERT INTO businesses (id, dek_enc, name_enc, country, language)"
            " VALUES ($1, '\\x00', '\\x00', 'CO', 'es')",
            uuid4(),
        )
        await apply_migrations(scratch, migrations_dir=tmp_path)  # type: ignore[call-arg]

        assert await conn.fetchval("SELECT count(*) FROM businesses WHERE probe IS NULL") == 1
        await conn.close()
    finally:
        await admin.execute("DROP DATABASE IF EXISTS vigia_d11 WITH (FORCE)")
        await admin.close()


@pytest.mark.edge("D12")
@_xfail("T-18: 'eliminar negocio' with two-step confirmation")
async def test_d12_delete_business_asks_for_confirmation_first(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "eliminar negocio"))

    assert reply is not None and recorder.analyzed == []
    assert await app_conn.fetchval("SELECT count(*) FROM businesses") == 1


# --- Rows whose piece does not exist yet ---------------------------------------------------
# Each row imports a symbol (``module:attr``) that only its own task creates, so another task
# landing the same module does not flip it. When the symbol lands the test XPASSes, strict
# turns that into a failure, and whoever lands it replaces the entry with a real test.


def _pending(row: pytest.MarkDecorator, task: str, target: str) -> object:
    return pytest.param(target, marks=[row, _xfail(task)], id=row.args[0])


PENDING = [
    _pending(
        pytest.mark.edge("A2"),
        "T-09: analyst gets first 4000 chars + truncated notice",
        "vigia.analyst.loop:truncate_for_analyst",
    ),
    _pending(
        pytest.mark.edge("A4"),
        "T-12: SAM runs once per image, one verdict",
        "vigia.analyst.tools:segment_images",
    ),
    _pending(
        pytest.mark.edge("A5"),
        "T-07: INVALID_ATTACHMENT -> doubtful + reply",
        "vigia.channel.templates:rejection_reply",
    ),
    _pending(
        pytest.mark.edge("A9"),
        "T-11: silent or < 1 s audio -> doubtful",
        "vigia.analyst.tools:transcribe",
    ),
    _pending(
        pytest.mark.edge("A10"),
        "T-11: audio > 5 min: first 5 min + notice",
        "vigia.analyst.tools:transcribe",
    ),
    _pending(
        pytest.mark.edge("A12"),
        "T-13: PDF_PROTECTED -> doubtful + medium signal",
        "vigia.analyst.tools:read_pdf",
    ),
    _pending(
        pytest.mark.edge("A13"),
        "T-13: PDF: first 5 pages + page 1 image",
        "vigia.analyst.tools:read_pdf",
    ),
    _pending(
        pytest.mark.edge("A15"),
        "T-17: inline forward: notice to send as .eml",
        "vigia.channel.email:inline_forward_notice",
    ),
    _pending(
        pytest.mark.edge("A16"),
        "T-17: nested .eml: notice, level 1 only",
        "vigia.channel.email:nested_eml_notice",
    ),
    _pending(
        pytest.mark.edge("A28"),
        "T-06c: 2000-line chat: last 300 + amounts",
        "vigia.signals.sequence:select_chat_lines",
    ),
    _pending(
        pytest.mark.edge("A29"),
        "T-06c: unknown chat format -> FORMAT",
        "vigia.signals.sequence:parse_chat_export",
    ),
    _pending(
        pytest.mark.edge("C1"),
        "T-09: 429: one retry with backoff, then degraded",
        "vigia.model.meta:MetaClient",
    ),
    _pending(
        pytest.mark.edge("C2"),
        "T-09: 5xx or 25 s timeout -> degraded doubtful",
        "vigia.model.meta:MetaClient",
    ),
    _pending(
        pytest.mark.edge("C3"),
        "T-09: 402/403 -> degraded + credits alert",
        "vigia.model.meta:MetaClient",
    ),
    _pending(
        pytest.mark.edge("C4"),
        "T-09: invalid JSON: one retry, then degraded",
        "vigia.analyst.loop:run_analyst",
    ),
    _pending(
        pytest.mark.edge("C5"),
        "T-09: forbidden tool -> FORBIDDEN as data",
        "vigia.analyst.loop:run_analyst",
    ),
    _pending(
        pytest.mark.edge("C6"),
        "T-09: 6-step cap -> verdict with what it has",
        "vigia.analyst.loop:run_analyst",
    ),
    _pending(
        pytest.mark.edge("C7"), "T-09: buscar_web cap 2 -> LIMIT", "vigia.analyst.loop:run_analyst"
    ),
    _pending(
        pytest.mark.edge("C8"),
        "T-12: SAM 10 s timeout -> no crops",
        "vigia.analyst.tools:segment_images",
    ),
    _pending(
        pytest.mark.edge("C9"),
        "T-11: VOICE_DOWN -> doubtful + ask to write",
        "vigia.analyst.tools:transcribe",
    ),
    _pending(
        pytest.mark.edge("C13"),
        "T-16: Redis down -> in-process analyst",
        "vigia.analyst.queue:enqueue",
    ),
    _pending(
        pytest.mark.edge("C14"),
        "T-16: 60 s visibility, 3 tries -> DLQ",
        "vigia.analyst.queue:enqueue",
    ),
    _pending(
        pytest.mark.edge("C16"),
        "T-09: resolver blocks private IPs (SSRF)",
        "vigia.channel.resolver:resolve_redirects",
    ),
    _pending(
        pytest.mark.edge("C17"),
        "T-17: Resend/IMAP down: retry for 1 h",
        "vigia.channel.email:send_with_retry",
    ),
    _pending(
        pytest.mark.edge("D1"),
        "T-08: business daily cap: reply + owner once",
        "vigia.policy.caps:check_caps",
    ),
    _pending(
        pytest.mark.edge("D2"),
        "T-08: sender cap + rejected attempt",
        "vigia.policy.caps:check_caps",
    ),
    _pending(
        pytest.mark.edge("D3"),
        "T-08: day boundary in the pack time zone",
        "vigia.policy.caps:check_caps",
    ),
    _pending(
        pytest.mark.edge("D5"),
        "T-17b: panel token reused or > 15 min -> 401",
        "vigia.services.panel:redeem_panel_token",
    ),
    _pending(
        pytest.mark.edge("D6"),
        "T-17c: rotated key valid 60 s more",
        "vigia.services.api_keys:rotate_key",
    ),
    _pending(
        pytest.mark.edge("D9"),
        "T-06b: eval colliding with KB fails",
        "vigia.signals.knowledge:load_knowledge",
    ),
]


@pytest.mark.parametrize("target", PENDING)
def test_pending_edge_row(target: str) -> None:
    module, attr = target.split(":")
    getattr(importlib.import_module(module), attr)
