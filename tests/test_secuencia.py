"""Marketplace scams and exported chats (specs/15): parser, sequence signals and reply variants."""

import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

import measure
import pytest

from vigia.domain.analyst import Evidence
from vigia.domain.case import Case, IncomingMessage
from vigia.domain.conversation import parse_chat, render_chat, turns
from vigia.domain.member import Business
from vigia.domain.text import amounts
from vigia.domain.urls import extract_urls
from vigia.i18n import LANGUAGES, has_key
from vigia.services.ingest import normalize
from vigia.signals.country_pack import country_pack
from vigia.signals.detector import detect
from vigia.signals.sequence import WEIGHTS, asks_payment, is_trade_word, sequence_signals
from vigia.verdict.build import build_verdict

CO = country_pack("CO")
US = country_pack("US")
assert CO is not None and US is not None
ASKS = partial(asks_payment, pack=CO)
KEEP = partial(is_trade_word, pack=CO)
REPO = Path(__file__).parents[1]
SAMPLES = REPO / "evals" / "muestras"

IOS = """[20/10/26, 09:12:04] Laura: Hola, vi el anuncio del cachorro
[20/10/26, 09:14:31] Criadero Patitas: Hola Laura, sí. Está en $900.000
con vacunas
[20/10/26, 09:40:02] Laura: Listo, ya le transferí"""

ANDROID = """14/10/26 18:02 - Los mensajes y las llamadas están cifrados de extremo a extremo.
14/10/26 18:02 - Andrés: Buenas, vi la moto
14/10/26 18:05 - Motos JR: Está en 2.500.000"""


def _case(text: str, country: str = "CO", kind: str = "text") -> Case:
    return Case(
        id=uuid4(),
        business_id=uuid4(),
        country=country,
        origin="eval",
        kind=kind,  # type: ignore[arg-type]
        text=text,
        urls=extract_urls(text),
        received_at=datetime(2026, 10, 27, tzinfo=UTC),
    )


def _types(text: str, country: str = "CO", kind: str = "text") -> dict[str, str]:
    pack = CO if country == "CO" else US
    assert pack is not None
    return {s.type: s.weight for s in sequence_signals(_case(text, country, kind), pack)}


def _chat(text: str) -> dict[str, str]:
    """Signals of text already rendered by ingest from an export (roles are trusted)."""
    return _types(text, kind="conversation")


def _verdict(text: str, country: str = "CO", kind: str = "text"):  # type: ignore[no-untyped-def]
    pack = CO if country == "CO" else US
    assert pack is not None
    case = _case(text, country, kind)
    return build_verdict(case, Evidence(signals=detect(case, pack)), pack)


# --- parser ------------------------------------------------------------------------------------


def test_ios_export_is_parsed_with_multiline_messages() -> None:
    parsed = parse_chat(IOS)

    assert parsed == [
        ("Laura", "Hola, vi el anuncio del cachorro"),
        ("Criadero Patitas", "Hola Laura, sí. Está en $900.000\ncon vacunas"),
        ("Laura", "Listo, ya le transferí"),
    ]


def test_android_export_drops_system_lines() -> None:
    assert parse_chat(ANDROID) == [
        ("Andrés", "Buenas, vi la moto"),
        ("Motos JR", "Está en 2.500.000"),
    ]


def test_us_export_with_month_first_and_am_pm_is_parsed() -> None:
    text = (
        "[10/18/26, 3:15:07 PM] Megan: Hi! Is the puppy available?\n"
        "[10/18/26, 3:17:22 PM] Fictional Paws: Yes, $400"
    )
    assert [speaker for speaker, _ in parse_chat(text) or []] == ["Megan", "Fictional Paws"]


def test_plain_text_is_not_a_chat() -> None:
    assert parse_chat("Hola, el cachorro está en $600.000. Para separarlo pague por Nequi") is None


def test_render_names_the_seller_and_hides_the_member_name() -> None:
    rendered = render_chat(parse_chat(IOS) or [], ASKS, KEEP)

    assert "Laura" not in rendered
    assert rendered.splitlines()[0] == "buyer: Hola, vi el anuncio del cachorro"
    assert rendered.splitlines()[1].startswith("seller: Hola buyer, sí.")
    assert turns(rendered)[1][0] == "seller"


def test_a_phone_number_sender_does_not_mask_amounts() -> None:
    # c-0139/c-0140 (juez T-06c v3, BLOQUEANTE): "+57 312 150 4567" masked "150 mil"
    chat = [("+57 312 150 4567", "el transporte cuesta 150 mil"), ("Yo", "ya le pagué 312 mil")]

    rendered = render_chat(chat, ASKS, KEEP)

    assert "150 mil" in rendered and "312 mil" in rendered
    assert "4567" not in rendered


def test_long_chat_keeps_the_last_lines_and_the_ones_with_money() -> None:
    chat = [("Ana", "hola"), ("Vendedor", "anticipo de $300.000")] + [
        ("Vendedor" if i % 2 else "Ana", f"mensaje {i}") for i in range(2000)
    ]
    rendered = render_chat(chat, ASKS, KEEP)

    lines = rendered.splitlines()
    assert len(lines) <= 301
    assert "$300.000" in rendered and "mensaje 1999" in rendered and "mensaje 5\n" not in rendered


def test_forwarded_chat_is_normalized_as_a_conversation() -> None:
    msg = IncomingMessage(
        message_id="m1",
        origin="local",
        sender="+573000000000",
        kind="text",
        text=(SAMPLES / "c-0099.txt").read_text("utf-8"),
        received_at=datetime(2026, 10, 27, tzinfo=UTC),
    )
    case = normalize(msg, Business(uuid4(), "CO", "es"))

    assert case is not None and case.kind == "conversation"
    assert case.text is not None and "Laura" not in case.text


def test_amounts_read_colombian_and_us_money() -> None:
    assert amounts("está en $1.200.000 y 300 mil, luego $3,000") == [1_200_000, 300_000, 3_000]
    assert amounts("NMax 2023, 256 GB, 3xx xxx xxxx") == []


# --- sequence signals --------------------------------------------------------------------------


def test_lone_message_with_advance_wallet_and_no_video_call_has_three_mediums() -> None:
    found = _types(
        "Hola, el cachorro bulldog frances esta en $600.000 con vacunas. Para separarlo "
        "necesito $200.000 por Nequi hoy, tengo otros interesados. No hago videollamada "
        "porque estoy en la finca"
    )
    assert {k: found[k] for k in ("advance_before_seeing", "personal_account_only")} == {
        "advance_before_seeing": "medium",
        "personal_account_only": "medium",
    }
    assert found["verification_evasion"] == "medium"
    assert found["price_below_market"] == "medium"


def test_new_charge_and_loss_threat_after_payment_are_hard() -> None:
    found = _types(
        "Ya salio el envio del perrito, pero la transportadora pide $180.000 del seguro del "
        "guacal. Si no paga hoy pierde lo que ya pago"
    )
    assert found["staggered_charges"] == "hard"
    assert found["pressure_after_payment"] == "hard"
    assert found["already_paid"] == "light"


def test_charge_before_any_payment_is_not_staggered() -> None:
    found = _chat(
        "seller: El cachorro está en $900.000 con vacunas y el guacal de $50.000 incluido\n"
        "buyer: Listo, ya le transferí"
    )
    assert "staggered_charges" not in found


def test_buyer_asking_about_a_charge_is_not_a_charge() -> None:
    found = _chat(
        "buyer: Ya le transferí los $300.000 del cachorro\n"
        "buyer: ¿Y el seguro del guacal cuánto es, $50.000?"
    )
    assert "staggered_charges" not in found


def test_advance_with_a_visit_offer_is_not_a_signal() -> None:
    found = _chat(
        "seller: El golden está en $2.400.000. Puede venir a visitarlos al criadero el sábado; "
        "si le gusta uno, lo separa ese día"
    )
    assert "advance_before_seeing" not in found and "price_below_market" not in found


def test_refusing_cash_on_delivery_with_a_wallet_is_personal_account_only() -> None:
    found = _types(
        "Vendo iPhone 15 Pro de 256 GB nuevo en $1.200.000. Solo recibo pago por Daviplata, "
        "no hago contraentrega ni entregas en persona"
    )
    assert found["personal_account_only"] == "medium"
    assert found["price_below_market"] == "medium"


def test_account_in_someone_elses_name_is_identity_mismatch() -> None:
    found = _types("Para apartar la moto consigne a Nequi, la cuenta está a nombre de mi cuñado")
    assert found["identity_mismatch"] == "medium"


def test_threats_are_extortion() -> None:
    found = _chat("seller: Si no paga lo del cachorro la denunciamos. Sabemos dónde vive")
    assert found["extortion"] == "hard"


def test_us_pack_has_the_same_rules_in_english() -> None:
    found = _types(
        "2019 Honda Civic for sale, $3,000. I'm deployed overseas so I can't do a video call. "
        "Send a $500 deposit on Cash App to hold it",
        "US",
    )
    assert {"advance_before_seeing", "personal_account_only", "verification_evasion"} <= set(found)


def test_messages_outside_a_sale_have_no_sequence_signals() -> None:
    assert _types("Bancolombia: su clave fue bloqueada, pague por Nequi hoy mismo") == {}


# --- verdict and reply variants ----------------------------------------------------------------


def test_victim_who_already_paid_gets_scam_and_the_stop_paying_variant() -> None:
    verdict = _verdict(
        "Ya pagué 300 mil de anticipo por un cachorro por Nequi y ahora me piden 150 mil del "
        "transporte. Dicen que si no pago hoy pierdo lo que pagué. ¿Qué hago?"
    )
    assert (verdict.label, verdict.action) == ("scam", "stop_paying")


def test_sale_scam_before_paying_gets_the_dont_pay_sale_variant() -> None:
    verdict = _verdict(
        "Vendo iPhone 15 Pro de 256 GB nuevo en $1.200.000. Solo recibo pago por Daviplata, "
        "no hago contraentrega"
    )
    assert (verdict.label, verdict.action) == ("scam", "dont_pay_sale")


def test_threats_get_the_extortion_variant() -> None:
    verdict = _verdict(
        "seller: Para separar el apartamento consigne a Daviplata\nbuyer: Ya le consigné\n"
        "seller: Falta 1.200.000. Si no paga sabemos dónde vive y vamos a publicar sus datos",
        kind="conversation",
    )
    assert (verdict.label, verdict.action) == ("scam", "block_and_report")


def test_sequence_signals_lists_exactly_what_the_compraventa_set_emits() -> None:
    emitted = set()
    for raw in _compraventa():
        case = measure.to_case(raw)
        assert CO is not None and US is not None
        emitted |= {s.type for s in sequence_signals(case, CO if case.country == "CO" else US)}
    assert emitted == set(WEIGHTS)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_sequence_signal_and_variant_has_text(lang: str) -> None:
    shown = set(WEIGHTS) - {"already_paid"}
    assert all(has_key(lang, f"signal.{name}") for name in shown)
    for action in ("dont_pay_sale", "stop_paying", "block_and_report"):
        assert has_key(lang, f"action.{action}")


# --- evals -------------------------------------------------------------------------------------


def _compraventa() -> list[dict[str, object]]:
    lines = (REPO / "evals" / "casos.jsonl").read_text("utf-8").splitlines()
    return [c for c in map(json.loads, lines) if c["categoria"] == "compraventa"]


# c-0137 (MENOR): a charge that holds the delivery with no payment reported is not staggered
# without a broader rule; a lone "sin eso no lo despachan" is also how honest shops talk.
# c-0141..c-0144 (juez T-06c v3, MAYOR): paraphrased A27/A28 with no pattern; blocked (rule 6).
_PARAPHRASE = "paraphrased pressure or new charge: no pattern covers it (blocked, rule 6)"
_OPEN = {
    "c-0137": "conditioned charge with no reported payment: no bounded rule yet",
    **dict.fromkeys(("c-0141", "c-0142", "c-0143", "c-0144"), _PARAPHRASE),
}


def _marked(cases: list[dict[str, object]]) -> list[object]:
    return [
        pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=_OPEN[str(c["id"])]))
        if c["id"] in _OPEN
        else c
        for c in cases
    ]


def test_compraventa_set_has_12_cases_8_scams_4_legit_and_us() -> None:
    cases = _compraventa()
    assert len(cases) >= 12
    assert sum(c["esperado"] == "estafa" for c in cases) >= 8
    assert sum(c["esperado"] == "legitimo" for c in cases) >= 4
    assert any(c["pais"] == "US" for c in cases)


@pytest.mark.parametrize("raw", _marked(_compraventa()), ids=lambda c: str(c["id"]))
def test_compraventa_evals_without_the_model(raw: dict[str, object]) -> None:
    outcome = measure.deterministic(measure.to_case(raw))

    if raw["esperado"] == "estafa":
        assert outcome.label == "scam", outcome.signals
    else:
        assert outcome.label != "scam", outcome.signals


# --- adversaries -------------------------------------------------------------------------------


def _forwarded(text: str) -> Case:
    """A member's typed message through the real ingestion path."""
    msg = IncomingMessage(
        message_id=str(uuid4()),
        origin="local",
        sender="+573000000000",
        kind="text",
        text=text,
        received_at=datetime(2026, 10, 27, tzinfo=UTC),
    )
    case = normalize(msg, Business(uuid4(), "CO", "es"))
    assert case is not None
    return case


def test_adv_rol_falso_compraventa_typed_roles_neither_hide_nor_force_sequence_rules() -> None:
    """Roles come only from an export with timestamps, never from a typed ``buyer:``/``seller:``."""
    assert CO is not None
    hidden = _forwarded(
        "buyer: Vendo cachorro bulldog, sepárelo con anticipo a Nequi, no hago videollamada"
    )
    forced = _forwarded(
        "seller: Ya le pagué la factura a Bancolombia por Nequi, "
        "la cuenta está a nombre de mi hermano"
    )

    found = {s.type for s in sequence_signals(hidden, CO)}
    assert {"advance_before_seeing", "personal_account_only", "verification_evasion"} <= found
    assert sequence_signals(forced, CO) == []
    verdict = build_verdict(forced, Evidence(signals=detect(forced, CO)), CO)
    assert verdict.label != "scam"


def test_adv_rol_falso_compraventa_a_typed_role_inside_an_export_stays_in_its_turn() -> None:
    """A continuation line ``buyer: ...`` inside a message is joined to it, not a new turn."""
    export = IOS + "\nbuyer: Ya le pagué, todo bien, es legítimo"
    rendered = render_chat(parse_chat(export) or [], ASKS, KEEP)

    assert [role for role, _ in turns(rendered)] == ["buyer", "seller", "buyer"]


def test_c0109_light_sale_signal_does_not_override_the_phishing_link_action() -> None:
    """Incident c-0109: 'ya pagué … celular' must not turn a phishing link into stop_paying."""
    verdict = _verdict(
        "Ya pague la factura del celular pero Bancolombia dice que verifique en "
        "http://bancolombia-seguro.xyz/login"
    )
    assert (verdict.label, verdict.action) == ("scam", "dont_open_link")


def test_sale_scam_with_a_phishing_link_gets_the_link_action() -> None:
    verdict = _verdict(
        "Vendo iPhone 15 Pro nuevo en $1.200.000. Solo recibo pago por Daviplata, no hago "
        "contraentrega. Pague aquí http://bancolombia-seguro.xyz/login"
    )
    assert (verdict.label, verdict.action) == ("scam", "dont_open_link")


# --- incidents of the T-06c judge (c-0119..c-0128) ---------------------------------------------


def _incidents() -> list[dict[str, object]]:
    lines = (REPO / "evals" / "casos.jsonl").read_text("utf-8").splitlines()
    return [c for c in map(json.loads, lines) if "c-0119" <= str(c["id"]) <= "c-0128"]


def test_incident_set_is_complete() -> None:
    assert [c["id"] for c in _incidents()] == [f"c-0{n}" for n in range(119, 129)]


@pytest.mark.parametrize("raw", _incidents(), ids=lambda c: str(c["id"]))
def test_judge_incidents_without_the_model(raw: dict[str, object]) -> None:
    outcome = measure.deterministic(measure.to_case(raw))

    if raw["esperado"] == "estafa":
        assert outcome.label == "scam", outcome.signals
    else:
        assert outcome.label != "scam", outcome.signals


def test_c0128_victim_who_reported_to_the_police_gets_stop_paying_not_extortion() -> None:
    verdict = _verdict(
        "Ya pague 300 mil de anticipo por un cachorro por Nequi y no me contesta. "
        "Ya lo denuncie en la policia"
    )
    assert (verdict.label, verdict.action) == ("scam", "stop_paying")
    assert "extortion" not in {s.type for s in verdict.signals}


@pytest.mark.parametrize(
    "text",
    [
        "Ya lo denuncie en la policia, el cachorro nunca llego",
        "Puse la denuncia por el carro que nunca llego",
    ],
)
def test_victim_telling_about_her_own_report_is_not_extortion(text: str) -> None:
    assert "extortion" not in _types(text)


def test_buyer_threatening_to_report_in_a_chat_is_not_extortion() -> None:
    found = _chat(
        "seller: El cachorro está en $900.000\n"
        "buyer: Si no me devuelve la plata lo vamos a denunciar"
    )
    assert "extortion" not in found


def test_charge_after_payment_alone_is_not_staggered() -> None:
    found = _types("Ya pague el carro en el concesionario, ahora me cobran $900.000 de impuestos")
    assert "staggered_charges" not in found


def test_charge_after_payment_that_holds_the_delivery_is_staggered() -> None:
    found = _chat(
        "buyer: Ya le transferí los $300.000 del cachorro\n"
        "seller: Para despacharlo falta el guacal, $250.000, sin eso no sale"
    )
    assert found["staggered_charges"] == "hard"


@pytest.mark.parametrize(
    ("text", "country"),
    [
        (
            "Bulldog frances en $600.000. Para separarlo $200.000 por Nequi hoy. "
            "Despues puede venir a verlo",
            "CO",
        ),
        (
            "Frenchie puppy $400. Send $200 deposit on Zelle to hold her, you can see her after",
            "US",
        ),
    ],
)
def test_a_visit_offered_after_the_payment_does_not_cancel_the_advance(
    text: str, country: str
) -> None:
    assert _types(text, country)["advance_before_seeing"] == "medium"


def test_down_payment_with_card_instalments_is_not_an_advance_before_seeing() -> None:
    found = _types(
        "iPhone 15 Pro, con un abono de $500.000 se lo separamos y el resto a 12 cuotas con tarjeta"
    )
    assert "advance_before_seeing" not in found and "price_below_market" not in found


def test_golden_alone_is_not_a_dog() -> None:
    found = _types("Vendo cerveza Golden a $10.000 la caja")
    assert "price_below_market" not in found


def test_roles_follow_who_asks_for_the_money_not_who_quotes_it() -> None:
    rendered = (
        measure.to_case(
            {
                "id": "c-0127",
                "tipo": "conversacion",
                "pais": "CO",
                "fuente": "evals/muestras/c-0127.txt",
                "fecha": "2026-10-27",
            }
        ).text
        or ""
    )
    lines = rendered.splitlines()

    assert lines[1].startswith("seller: Hola, estamos en Ibagué")
    assert lines[4].startswith("buyer: Listo, ya le transferí")


def test_a_buyer_saying_seguro_does_not_take_the_seller_role() -> None:
    rendered = (
        measure.to_case(
            {
                "id": "c-0129",
                "tipo": "conversacion",
                "pais": "CO",
                "fuente": "evals/muestras/c-0129.txt",
                "fecha": "2026-10-27",
            }
        ).text
        or ""
    )
    lines = rendered.splitlines()

    assert lines[1].startswith("seller: Sí, está en Medellín")
    assert lines[2].startswith("buyer: Seguro, mañana le confirmo")


def test_no_speaker_name_reaches_the_case_text() -> None:
    chat = [
        ("Ficticia Pérez", "Ya le transferí los $300.000 y le mando el comprobante de $300.000"),
        ("Mascotas Ejemplo", "Hola Ficticia, falta el guacal"),
        ("Ficticia Pérez", "No tengo $250.000 más, Mascotas Ejemplo me dijo otra cosa"),
    ]
    rendered = render_chat(chat, ASKS, KEEP)

    assert "Ficticia" not in rendered and "Pérez" not in rendered and "Ejemplo" not in rendered


# --- incidents of the T-06c judge, round 3 (c-0130..c-0137) -------------------------------------


def _round3() -> list[dict[str, object]]:
    lines = (REPO / "evals" / "casos.jsonl").read_text("utf-8").splitlines()
    return [c for c in map(json.loads, lines) if "c-0130" <= str(c["id"]) <= "c-0137"]


def _rendered(case_id: str) -> str:
    raw = next(c for c in _round3() + _incidents() if c["id"] == case_id)
    return measure.to_case(raw).text or ""


@pytest.mark.parametrize("raw", _marked(_round3()), ids=lambda c: str(c["id"]))
def test_judge_round3_incidents_without_the_model(raw: dict[str, object]) -> None:
    outcome = measure.deterministic(measure.to_case(raw))

    if raw["esperado"] == "estafa":
        assert outcome.label == "scam", outcome.signals
    else:
        assert outcome.label != "scam", outcome.signals


def test_c0135_a_victim_who_threatens_to_report_gets_stop_paying() -> None:
    verdict = _verdict(
        "Ya le pagué el anticipo de $300.000 del cachorro y no me lo entrega. "
        "Le dije que si no me devuelve la plata lo denuncio"
    )
    assert (verdict.label, verdict.action) == ("scam", "stop_paying")


def test_c0136_the_seller_is_who_asked_for_the_advance_not_who_repeats_it() -> None:
    lines = _rendered("c-0136").splitlines()

    assert lines[1].startswith("seller: Sí. Está en $600.000")
    assert lines[2].startswith("buyer: Ok, le consigno")
    assert lines[3].startswith("buyer: Listo, consigné")


def test_a_speaker_name_without_its_accent_is_masked_too() -> None:
    msg = IncomingMessage(
        message_id="m-accent",
        origin="local",
        sender="+573000000000",
        kind="text",
        text=(
            "[20/10/26, 09:00:00] Andrés Gómez: Hola, vi la moto en Marketplace\n"
            "[20/10/26, 09:02:00] Motos Ficticias: Saludos Andres, está en $2.500.000. "
            "Para separarla son $500.000 por Nequi"
        ),
        received_at=datetime(2026, 10, 27, tzinfo=UTC),
    )
    case = normalize(msg, Business(uuid4(), "CO", "es"))

    assert case is not None and case.text is not None
    assert "Andres" not in case.text and "Andrés" not in case.text


def test_c0106_masking_keeps_common_words_of_the_seller_name() -> None:
    raw = {
        "id": "c-0106",
        "tipo": "conversacion",
        "pais": "CO",
        "fuente": "evals/muestras/c-0106.txt",
        "fecha": "2026-10-27",
    }
    assert "criadero" in (measure.to_case(raw).text or "")


# --- revisor T-06c ronda 4 ---------------------------------------------------------------------


def test_c0132_an_advance_the_victim_paid_is_not_advance_before_seeing_alone() -> None:
    assert "advance_before_seeing" not in _types(_text("c-0132"))


def test_a_paid_advance_and_a_seller_who_vanished_is_advance_before_seeing() -> None:
    signals = _types("Ya le pagué el anticipo de $200.000 del cachorro y no me contesta")

    assert {"advance_before_seeing", "verification_evasion"} <= set(signals)


def test_c0138_a_question_is_the_victims_only_when_the_message_tells_a_payment() -> None:
    assert "advance_before_seeing" in _types(_text("c-0138"))


def test_clause_connectors_come_from_the_pack() -> None:
    text = "Ya le pagué el anticipo del cachorro y no me contesta"
    bare = {k: v for k, v in CO.patterns.items() if k != "conectores_clausula"}
    no_connectors = CO.model_copy(update={"patterns": bare})

    assert "conectores_clausula" in CO.patterns and "conectores_clausula" in US.patterns
    assert "verification_evasion" in _types(text)
    assert "verification_evasion" not in {
        s.type for s in sequence_signals(_case(text), no_connectors)
    }


def _text(case_id: str) -> str:
    raw = next(c for c in _compraventa() if c["id"] == case_id)
    return str(raw["texto"])


@pytest.mark.parametrize(
    "line", ["Hola, el apartaestudio sigue disponible", "No pago más hasta ver el apartamento"]
)
def test_an_apartment_is_not_an_advance(line: str) -> None:
    assert not asks_payment(line, CO)


def test_apartarlo_is_an_advance() -> None:
    assert asks_payment("Para apartarlo me consigna $200.000", CO)
