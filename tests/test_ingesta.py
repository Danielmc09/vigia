"""Ingestion: whitelist, normalization to Case, URLs, email, dedupe (specs/01, specs/02)."""

from email.message import EmailMessage

import asyncpg
import pytest

from tests.conftest import Recorder, message
from vigia.domain.urls import extract_urls
from vigia.i18n import t
from vigia.services.ingest import handle

OWNER = "+573000000301"
STRANGER = "+573000000399"


# --- URLs (pure) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Verifique en https://bancolombia-seguro.co/verificar",
            ["https://bancolombia-seguro.co/verificar"],
        ),
        ("Reclamalo aqui bit.ly/3xQnq", ["bit.ly/3xQnq"]),
        ("Escribeme a wa.me/573000000301 ya", ["wa.me/573000000301"]),
        ("Pague en www.dian-pagos.com.co/deuda.", ["www.dian-pagos.com.co/deuda"]),
        ("Ingrese a NEQUI-Pagos.co/Giro, gracias", ["nequi-pagos.co/Giro"]),
        ("HTTP://Servientrega-Guia.CO/rastreo?id=9", ["http://servientrega-guia.co/rastreo?id=9"]),
        (
            "ver https://tienda.example/x?utm_source=sms&id=3&fbclid=abc",
            ["https://tienda.example/x?id=3"],
        ),
        ("(tinyurl.com/abc12)", ["tinyurl.com/abc12"]),
        (
            "USPS: redelivery at https://usps-redelivery.info/track now",
            ["https://usps-redelivery.info/track"],
        ),
        ("Refund: amzn-refund.net/claim or amzn-refund.net/claim", ["amzn-refund.net/claim"]),
        ("Saldo 1.500.000 escriba a pagos@aceros.example", []),
        ("Ingrese a http://192.0.2.45:8080/bogota ya", ["http://192.0.2.45:8080/bogota"]),
        ("Version 10.2.3.4 instalada", []),  # a bare IP needs a scheme to be a link
    ],
)
def test_urls_are_extracted_tolerantly_and_cleaned(text: str, expected: list[str]) -> None:
    assert extract_urls(text) == expected


# --- Whitelist and normalization against Postgres ---------------------------------

pytestmark = pytest.mark.anyio


async def _owner(conn: asyncpg.Connection, recorder: Recorder) -> None:
    await handle(conn, recorder.ports, message(OWNER, "hola Ferreteria El Tornillo CO"))


async def test_unknown_sender_is_ignored_and_recorded(
    admin_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    before = await admin_conn.fetchval("SELECT count(*) FROM rejected_attempts")

    reply = await handle(admin_conn, recorder.ports, message(STRANGER, "Su paquete esta retenido"))

    assert reply is None
    assert recorder.analyzed == []
    assert await admin_conn.fetchval("SELECT count(*) FROM rejected_attempts") == before + 1


async def test_app_role_cannot_read_rejected_attempts(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await handle(app_conn, recorder.ports, message(STRANGER, "Su paquete esta retenido"))

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.fetch("SELECT * FROM rejected_attempts")


async def test_text_from_member_becomes_a_case_with_urls(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    text = "Bancolombia: su cuenta sera bloqueada. Verifique en bancolombia-seguro.co/v"

    reply = await handle(app_conn, recorder.ports, message(OWNER, text))

    assert reply is None
    [case] = recorder.analyzed
    assert (case.kind, case.country, case.text) == ("text", "CO", text)
    assert case.urls == ["bancolombia-seguro.co/v"]
    assert await app_conn.fetchval("SELECT count(*) FROM cases") == 1


async def test_message_that_is_only_a_link_is_a_url_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    await handle(app_conn, recorder.ports, message(OWNER, " https://bit.ly/3xQnq "))

    assert recorder.analyzed[0].kind == "url"


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (("image", "image/jpeg"), "image"),
        (("audio", "audio/ogg"), "audio"),
        (("document", "application/pdf"), "pdf"),
    ],
)
async def test_attachment_kind_comes_from_the_channel_and_mime(
    app_conn: asyncpg.Connection, recorder: Recorder, channel: tuple[str, str], expected: str
) -> None:
    await _owner(app_conn, recorder)
    kind, mime = channel

    msg = message(OWNER, None, kind=kind, mime=mime, attachment_ref="ref-1")
    await handle(app_conn, recorder.ports, msg)

    [case] = recorder.analyzed
    assert case.kind == expected
    assert [(a.ref, a.mime) for a in case.attachments] == [("ref-1", mime)]


async def test_unsupported_document_gets_a_reply_and_no_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    msg = message(OWNER, None, kind="document", mime="application/zip", attachment_ref="r")
    reply = await handle(app_conn, recorder.ports, msg)

    assert reply == t("es", "case.unsupported")
    assert recorder.analyzed == []


def _eml() -> bytes:
    mail = EmailMessage()
    mail["From"] = "Banco Prueba <alertas@banco-prueba.example>"
    mail["Reply-To"] = "soporte@otro-dominio.example"
    mail["Return-Path"] = "<rebote@otro-dominio.example>"
    mail["Authentication-Results"] = "mx.example; spf=fail; dkim=none; dmarc=fail"
    mail["Subject"] = "Cuenta suspendida"
    mail.set_content("Actualice sus datos en banco-prueba-seguro.example/login")
    return mail.as_bytes()


async def test_eml_attachment_becomes_email_case_with_headers(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    await handle(app_conn, recorder.ports, message(OWNER, None, kind="email", eml_bytes=_eml()))

    [case] = recorder.analyzed
    assert case.kind == "email" and case.email is not None
    assert case.email.with_headers is True
    assert (case.email.sender, case.email.display_name) == (
        "alertas@banco-prueba.example",
        "Banco Prueba",
    )
    assert case.email.reply_to == "soporte@otro-dominio.example"
    assert case.email.auth_results is not None and "spf=fail" in case.email.auth_results
    assert case.urls == ["banco-prueba-seguro.example/login"]


async def test_inline_forwarded_email_has_no_headers(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    await handle(
        app_conn, recorder.ports, message(OWNER, "Estimado cliente, pague ya", kind="email")
    )

    [case] = recorder.analyzed
    assert case.kind == "email" and case.email is not None
    assert case.email.with_headers is False
    assert case.text == "Estimado cliente, pague ya"


async def test_repeated_message_id_is_not_analyzed_again(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)
    msg = message(OWNER, "Su paquete esta retenido en bodega", message_id="m-dup-1")

    first = await handle(app_conn, recorder.ports, msg)
    second = await handle(app_conn, recorder.ports, msg)

    assert first is None
    assert second == t("es", "case.duplicate")
    assert len(recorder.analyzed) == 1
    assert await app_conn.fetchval("SELECT count(*) FROM cases") == 1


async def test_command_is_intercepted_and_creates_no_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "historial"))

    assert reply == t("es", "history.empty")
    assert recorder.analyzed == []
    assert await app_conn.fetchval("SELECT count(*) FROM cases") == 0


async def test_hello_with_text_from_member_is_analyzed_as_a_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _owner(app_conn, recorder)

    reply = await handle(app_conn, recorder.ports, message(OWNER, "Hola mami, cambie de numero"))

    assert reply is None
    assert [c.text for c in recorder.analyzed] == ["Hola mami, cambie de numero"]
