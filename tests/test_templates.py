"""WhatsApp message templates (specs/05): code-built from i18n, never model text."""

import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from vigia.channel.templates import (
    repeat_message,
    team_alert_message,
    team_alert_params,
    whatsapp_message,
)
from vigia.domain.case import Case, EmailMeta
from vigia.domain.escalation import Escalation
from vigia.domain.signal import Signal
from vigia.domain.verdict import Verdict
from vigia.i18n import t


def _verdict(**over: object) -> Verdict:
    base: dict[str, object] = {
        "case_id": str(uuid4()),
        "business_id": str(uuid4()),
        "label": "scam",
        "confidence": 0.9,
        "user_explanation": "Esto es una estafa.",
        "action": "dont_open_link",
        "official_channel": "Linea 01 8000 del banco",
    }
    base.update(over)
    return Verdict.model_validate(base)


def _escalation(target: str) -> Escalation:
    now = datetime.now(UTC)
    return Escalation("AB12CD", uuid4(), "report", target, now)


def test_whatsapp_message_includes_explanation_action_and_short_case_number() -> None:
    verdict = _verdict()
    msg = whatsapp_message(verdict, "es", case_ref=14)
    assert "Esto es una estafa." in msg
    assert "no abras el link" in msg
    assert msg.endswith("caso 14")
    assert verdict.case_id not in msg


def test_whatsapp_message_short_case_number_in_english() -> None:
    assert whatsapp_message(_verdict(), "en", case_ref=3).endswith("case 3")


@pytest.mark.parametrize(
    ("lang", "head", "tail"),
    [
        ("es", "Ya lo revisé el 31/10 a las 09:30:", "caso 7"),
        ("en", "I already checked this on 10/31 at 09:30:", "case 7"),
    ],
)
def test_repeat_message_says_when_in_colombia_time_then_the_same_verdict(
    lang: str, head: str, tail: str
) -> None:
    at = datetime(2026, 10, 31, 14, 30, tzinfo=UTC)
    msg = repeat_message(_verdict(), lang, 7, at)
    assert msg.startswith(head + "\n")
    assert msg.endswith(tail)


def test_no_confirm_line_without_an_escalation() -> None:
    assert "confirmar" not in whatsapp_message(_verdict(escalation_id="AB12CD"), "es")


def test_confirm_line_names_the_escalation_target_not_the_official_channel() -> None:
    msg = whatsapp_message(_verdict(), "es", _escalation("csirt@example.org"))
    confirm_line = next(line for line in msg.splitlines() if "confirmar" in line)
    assert "confirmar AB12CD" in confirm_line
    assert "csirt@example.org" in confirm_line
    assert "Linea 01 8000" not in confirm_line


REPORTER = "+57 300 000 0001"
SIMIT = (
    "Multa pendiente SIMIT. Pague hoy en https://simit-pagos.click/pago?doc=123"
    " o llame al +57 300 000 1234"
)


QUOTE_MAX = 120  # the fixed cut the alert promises
SIMIT_QUOTE = "Multa pendiente SIMIT. Pague hoy en simit-pagos[.]click o llame al +57 300 ***"


def _scam_case(text: str = SIMIT, **over: object) -> Case:
    base: dict[str, object] = {
        "id": uuid4(),
        "business_id": uuid4(),
        "country": "CO",
        "origin": "wazenda",
        "sender": REPORTER,
        "kind": "text",
        "text": text,
        "urls": ["https://simit-pagos.click/pago?doc=123"],
        "received_at": datetime.now(UTC),
    }
    base.update(over)
    return Case.model_validate(base)


_NO_LINK = [Signal(type="urgency", weight="medium", source="patterns")]


def _scam_verdict(brand: str | None = "SIMIT") -> Verdict:
    signals = [
        Signal(type="urgency", weight="medium", source="patterns"),
        Signal(
            type="brand_off_domain",
            weight="hard",
            source="urls",
            evidence={
                "url": "https://simit-pagos.click/pago?doc=123",
                "domain": "simit-pagos.click",
            },
        ),
        Signal(type="credentials_request", weight="hard", source="analyst"),
    ]
    return _verdict(signals=signals, impersonated_brand=brand)


def test_team_alert_message_renders_in_english() -> None:
    msg = team_alert_message("en", "Ana", _scam_verdict("Chase"), _scam_case())
    assert "Chase" in msg


@pytest.mark.parametrize("lang", ["es", "en"])
def test_t10d_team_alert_carries_the_hook_and_the_disarmed_indicator(lang: str) -> None:
    """T-10d: the team can recognize the scam without getting it again."""
    msg = team_alert_message(lang, "Ana", _scam_verdict(), _scam_case())

    assert "simit-pagos[.]click" in msg
    assert "+57 300 ***" in msg
    assert t(lang, "team_alert.disarmed") in msg
    # the hook is the strongest deterministic signal, phrased by code, never the analyst's
    assert t(lang, "signal.brand_off_domain") in msg
    assert t(lang, "signal.credentials_request") not in msg
    # the scammer's words only as a quote; nothing clickable, nobody's number
    assert "doc=123" not in msg
    assert not re.search(r"https?://|www\.|\w\.(click|com|co)\b", msg)
    assert "000 1234" not in msg
    assert REPORTER not in msg and "0001" not in msg


def test_t10d_an_email_scam_shows_the_sender_domain_disarmed() -> None:
    case = _scam_case(
        "Adjunto factura con la nueva cuenta",
        kind="email",
        urls=[],
        email=EmailMeta(sender="pagos@proveedor-falso.co", with_headers=True),
    )
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), case)

    assert "proveedor-falso[.]co" in msg
    assert "pagos@" not in msg


def test_t10d_without_link_or_number_the_alert_has_no_indicator_line() -> None:
    case = _scam_case("Mamá, cambié de número, mándame plata", urls=[])
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), case)

    assert t("es", "team_alert.disarmed") not in msg


def test_t10d_the_indicator_never_points_at_the_official_domain() -> None:
    urls = ["https://www.bancolombia.com", "https://bancolombia-seguro.click/x"]
    signals = [
        Signal(
            type="official_domain",
            weight="light",
            source="urls",
            evidence={"domain": "bancolombia.com"},
        ),
        Signal(
            type="brand_off_domain",
            weight="hard",
            source="urls",
            evidence={"domain": "bancolombia-seguro.click"},
        ),
    ]
    verdict = _verdict(signals=signals, impersonated_brand="Bancolombia")
    msg = team_alert_message("es", "Ana", verdict, _scam_case(text="Ingrese ya", urls=urls))

    assert "bancolombia-seguro[.]click" in msg
    assert "bancolombia[.]com" not in msg


def test_t10d_an_account_number_is_not_shown_as_a_phone() -> None:
    case = _scam_case(text="Nueva cuenta para el pago: 0123-4567-8901", urls=[])
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), case)

    assert t("es", "team_alert.disarmed") not in msg  # no indicator line
    assert "4567" not in msg  # and the quote masks it


@pytest.mark.parametrize(
    ("phone", "shown"), [("+573000001234", "+57 300 ***"), ("300 000 1234", "300 ***")]
)
def test_t10d_the_phone_is_cut_by_digits_not_characters(phone: str, shown: str) -> None:
    case = _scam_case(text=f"Llame ya al {phone}", urls=[])
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), case)

    assert shown in msg
    assert "1234" not in msg


def test_t10d_an_official_site_over_http_is_neither_hook_nor_indicator() -> None:
    """Revisor T-10d: http_without_tls carries the official domain too."""
    signals = [
        Signal(
            type="official_domain",
            weight="light",
            source="urls",
            evidence={"domain": "bancolombia.com"},
        ),
        Signal(
            type="http_without_tls",
            weight="light",
            source="urls",
            evidence={"domain": "bancolombia.com"},
        ),
        Signal(
            type="could_not_verify",
            weight="light",
            source="urls",
            evidence={"domain": "pagos-ya.click"},
        ),
    ]
    urls = ["http://bancolombia.com", "https://pagos-ya.click/x"]
    verdict = _verdict(signals=signals, impersonated_brand="Bancolombia")
    msg = team_alert_message("es", "Ana", verdict, _scam_case(text="Ingrese ya", urls=urls))

    assert "bancolombia[.]com" not in msg
    assert t("es", "signal.official_domain") not in msg


def test_t10d_a_short_plus_number_is_not_shown_as_a_phone() -> None:
    case = _scam_case(text="Código +12 345 678", urls=[])
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), case)

    assert t("es", "team_alert.disarmed") not in msg


@pytest.mark.parametrize(("lang", "language"), [("es", "es"), ("en", "en_US")])
def test_t18b_template_params_are_one_line_each_and_carry_the_quote(
    lang: str, language: str
) -> None:
    """alerta_equipo_estafa: business, reporter, one-sentence summary, case number."""
    case = _scam_case().model_copy(update={"ref": 14})
    got, params = team_alert_params(lang, ("Ferreteria El Tornillo", "Ana"), _scam_verdict(), case)

    assert got == language
    assert params == [
        "Ferreteria El Tornillo",
        "Ana",
        t(lang, "team_alert.quote", quote=SIMIT_QUOTE),
        "14",
    ]


def test_t18b_without_a_hook_the_summary_says_how_it_came() -> None:
    case = _scam_case("Mamá, cambié de número", urls=[], kind="image")
    case = case.model_copy(update={"ref": 2})
    _, params = team_alert_params("es", ("Negocio", "Ana"), _verdict(signals=[]), case)

    assert params[2] == "Llegó en una captura."
    assert all(p and "\n" not in p for p in params)  # Meta rejects empty or multiline params


def test_incident_the_alert_quotes_what_arrived_with_link_and_number_disarmed() -> None:
    """Incident 31 oct: the alert said why it was flagged, never what arrived."""
    msg = team_alert_message("es", "Ana", _scam_verdict(), _scam_case())

    assert f"Dice: “{SIMIT_QUOTE}”" in msg.splitlines()
    assert "simit-pagos.click" not in msg and "doc=123" not in msg
    assert "1234" not in msg
    # the quote replaces the system's criterion, it does not pile on top of it
    assert t("es", "signal.brand_off_domain") not in msg


def test_incident_the_kind_reads_inside_the_sentence() -> None:
    """Incident 31 oct: "(mensaje)" in parentheses read like a form field."""
    for kind, phrase in [
        ("text", "un mensaje de texto"),
        ("audio", "una nota de voz"),
        ("image", "una captura"),
        ("pdf", "un PDF"),
    ]:
        first = team_alert_message("es", "Ana", _scam_verdict(), _scam_case(kind=kind))
        first = first.splitlines()[0]
        assert f"estafa de SIMIT que llegó en {phrase}." in first
        assert "(" not in first


def test_incident_a_long_message_is_cut_at_a_whole_word() -> None:
    words = "Estimado cliente su cuenta presenta un bloqueo preventivo por seguridad " * 4
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), _scam_case(words, urls=[]))
    quote = next(line for line in msg.splitlines() if line.startswith("Dice: “"))
    body = quote.removeprefix("Dice: “").removesuffix("…”")

    assert quote.endswith("…”")
    assert len(body) <= QUOTE_MAX
    assert words.startswith(body + " ")  # ends on a whole word


def test_incident_accounts_ids_codes_and_emails_are_masked_in_the_quote() -> None:
    text = (
        "Consigne $1.500.000 a la cuenta 0123-4567-8901, cédula 1.020.304.050,"
        " código 482913, y escriba a pagos@proveedor-falso.co"
    )
    msg = team_alert_message("es", "Ana", _verdict(signals=_NO_LINK), _scam_case(text, urls=[]))

    assert "Dice: “" in msg
    for leak in ("0123", "4567", "8901", "020", "304", "482913", "proveedor-falso.co"):
        assert leak not in msg
    assert "proveedor-falso[.]co" in msg
    assert "$1.500.000" in msg  # an amount is how the scam reads, not who it is


def test_incident_a_quote_with_nothing_readable_falls_back_to_the_hook() -> None:
    case = _scam_case("https://simit-pagos.click/pago?doc=123 3000001234")
    msg = team_alert_message("es", "Ana", _scam_verdict(), case)

    assert "Dice:" not in msg
    assert t("es", "team_alert.hook", hook=t("es", "signal.brand_off_domain")) in msg


@pytest.mark.parametrize("kind", ["image", "pdf", "conversation"])
def test_incident_model_read_or_chat_text_is_never_quoted(kind: str) -> None:
    """Rule 14: a screenshot's or PDF's text was written by Spark, not by the scammer."""
    msg = team_alert_message("es", "Ana", _scam_verdict(), _scam_case(kind=kind))

    assert "Dice:" not in msg and "Multa pendiente" not in msg
    assert t("es", "signal.brand_off_domain") in msg


def test_incident_the_template_summary_quotes_on_one_line() -> None:
    case = _scam_case("Su paquete está retenido.\nPague el envío hoy mismo", urls=[])
    _, params = team_alert_params("es", ("Negocio", "Ana"), _verdict(signals=[]), case)

    assert params[2] == "Dice: “Su paquete está retenido. Pague el envío hoy mismo”"
