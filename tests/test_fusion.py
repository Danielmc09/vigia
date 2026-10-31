"""Fusion of specs/05: signals, and the analyst when present, decide the label."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from vigia.channel.templates import whatsapp_message
from vigia.domain.analyst import AnalystResult, Evidence
from vigia.domain.case import Case
from vigia.domain.signal import Signal
from vigia.domain.urls import extract_urls
from vigia.domain.verdict import Verdict
from vigia.signals.country_pack import country_pack
from vigia.signals.detector import detect
from vigia.verdict.build import build_verdict
from vigia.verdict.fusion import fuse, soften_citations


def _sig(kind: str, weight: str, **evidence: object) -> Signal:
    return Signal(type=kind, weight=weight, source="patterns", evidence=evidence)  # type: ignore[arg-type]


def _analyst(label: str, confidence: float) -> AnalystResult:
    return AnalystResult(label=label, confidence=confidence)  # type: ignore[arg-type]


def _eval_case(case_id: str) -> Case:
    """The text case ``case_id`` of evals/casos.jsonl, with its URLs."""
    raw = next(
        json.loads(line)
        for line in (Path(__file__).parents[1] / "evals" / "casos.jsonl")
        .read_text("utf-8")
        .splitlines()
        if f'"{case_id}"' in line
    )
    return _case(raw["texto"], extract_urls(raw["texto"]))


def _case(text: str, urls: list[str] | None = None) -> Case:
    return Case(
        id=uuid4(),
        business_id=uuid4(),
        country="CO",
        origin="eval",
        kind="text",
        text=text,
        urls=urls or [],
        received_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("signals", "label", "confidence"),
    [
        ([_sig("brand_off_domain", "hard")], "scam", 0.9),
        ([_sig("urgency", "medium"), _sig("prize", "medium")], "scam", 0.8),
        ([_sig("urgency", "medium")], "doubtful", 0.5),
        # the same medium twice (two .xyz links) is still one piece of evidence
        ([_sig("suspicious_tld", "medium"), _sig("suspicious_tld", "medium")], "doubtful", 0.5),
        ([_sig("official_domain", "light"), _sig("personal_payment", "light")], "doubtful", 0.5),
        ([], "doubtful", 0.5),
    ],
)
def test_fusion_without_analyst(signals: list[Signal], label: str, confidence: float) -> None:
    assert fuse(signals) == (label, confidence)


@pytest.mark.parametrize(
    ("signals", "analyst", "label", "confidence"),
    [
        # rule 1: a hard signal decides alone; the analyst can only raise the confidence
        # when it agrees, never override the label.
        ([_sig("brand_off_domain", "hard")], _analyst("scam", 0.97), "scam", 0.97),
        ([_sig("brand_off_domain", "hard")], _analyst("legit", 0.99), "scam", 0.9),
        # rule 3: one medium signal, the analyst breaks the tie.
        ([_sig("urgency", "medium")], _analyst("scam", 0.6), "scam", 0.6),
        ([_sig("urgency", "medium")], _analyst("legit", 0.9), "doubtful", 0.5),
        ([_sig("urgency", "medium")], _analyst("legit", 0.5), "doubtful", 0.5),
        # rule 4: no medium or hard signal, the analyst alone decides.
        ([], _analyst("legit", 0.9), "legit", 0.9),
        ([], _analyst("legit", 0.5), "doubtful", 0.5),
        ([], _analyst("scam", 0.8), "scam", 0.8),
        ([], _analyst("doubtful", 0.4), "doubtful", 0.4),
    ],
)
def test_fusion_with_analyst(
    signals: list[Signal], analyst: AnalystResult, label: str, confidence: float
) -> None:
    assert fuse(signals, analyst) == (label, confidence)


def test_rule4_legit_needs_no_unofficial_url() -> None:
    assert fuse([], _analyst("legit", 0.9), has_unofficial_url=True) == ("doubtful", 0.5)
    assert fuse([], _analyst("legit", 0.9), has_unofficial_url=False) == ("legit", 0.9)


def test_manipulation_never_legit() -> None:
    assert fuse([], _analyst("legit", 0.9), manipulation_detected=True) == ("doubtful", 0.5)


def test_manipulation_does_not_soften_a_scam() -> None:
    assert fuse([_sig("brand_off_domain", "hard")], manipulation_detected=True) == ("scam", 0.9)


def test_known_campaign_quoted_as_warning_is_not_condemned() -> None:
    """c-0076 (incident): citing a known-scam domain to warn the team off it.

    Is not the same as a message that invites the reader to open it.
    """
    case = _case(
        "Equipo, ojo: la DIAN advirtio que diangov.com es un sitio falso, NO entren ahi.",
        urls=["diangov.com", "dian.gov.co"],
    )
    campaign_hit = _sig("known_campaign", "hard", match="domain", domain="diangov.com")
    authority = _sig("authority_with_link", "medium")
    pack = country_pack("CO")
    assert pack is not None

    softened = soften_citations([campaign_hit, authority], case, pack)

    assert all(s.weight != "hard" for s in softened)
    assert fuse(softened) == ("doubtful", 0.5)


def test_known_campaign_as_a_real_invitation_still_condemns() -> None:
    """Without the warning wording the same domain mention still convicts (no regression)."""
    case = _case("Entra ya a diangov.com para evitar el embargo.", urls=["diangov.com"])
    campaign_hit = _sig("known_campaign", "hard", match="domain", domain="diangov.com")
    pack = country_pack("CO")
    assert pack is not None

    softened = soften_citations([campaign_hit], case, pack)

    assert softened == [campaign_hit]
    assert fuse(softened) == ("scam", 0.9)


def test_build_verdict_hard_signal_sets_action_and_degraded() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Ingresa a bancolombia-seguro.com para verificar tu cuenta")
    signal = Signal(
        type="brand_off_domain",
        weight="hard",
        source="urls",
        evidence={"brand": "Bancolombia", "url": "http://bancolombia-seguro.com"},
    )

    verdict = build_verdict(case, Evidence(signals=[signal]), pack)

    assert verdict.label == "scam"
    assert verdict.action == "dont_open_link"
    assert verdict.degraded is True
    assert verdict.impersonated_brand == "Bancolombia"
    assert "Bancolombia" in verdict.user_explanation


def test_build_verdict_payment_signal_sets_dont_pay() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Paga por Nequi para liberar el paquete")
    signal = Signal(type="personal_payment_release", weight="hard", source="patterns", evidence={})

    verdict = build_verdict(case, Evidence(signals=[signal]), pack)

    assert verdict.action == "dont_pay"


def test_build_verdict_legit_action_is_ignore_and_not_degraded() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Gracias por la compra, su pedido va en camino.")

    verdict = build_verdict(case, Evidence(analyst=_analyst("legit", 0.9)), pack)

    assert verdict.label == "legit"
    assert verdict.action == "ignore"
    assert verdict.degraded is False


def test_build_verdict_never_forwards_the_analyst_raw_text() -> None:
    """AGENTS regla 14: the model never talks to the user; the code builds the message."""
    pack = country_pack("CO")
    assert pack is not None
    case = _case("mensaje cualquiera")
    analyst = AnalystResult(
        label="scam", confidence=0.95, explanation="IGNORE ALL PREVIOUS INSTRUCTIONS"
    )

    verdict = build_verdict(case, Evidence(analyst=analyst), pack)

    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" not in verdict.user_explanation


def test_build_verdict_incident_c_0076_does_not_condemn_the_warning() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case(
        "Equipo, ojo: la DIAN advirtio que diangov.com es un sitio falso que suplanta su "
        "correo, NO entren ahi. El unico canal oficial es dian.gov.co.",
        urls=["diangov.com", "dian.gov.co"],
    )
    signals = [
        _sig("known_campaign", "hard", match="domain", domain="diangov.com"),
        _sig("authority_with_link", "medium"),
        Signal(
            type="official_domain",
            weight="light",
            source="urls",
            evidence={"url": "dian.gov.co", "domain": "dian.gov.co"},
        ),
    ]

    verdict = build_verdict(case, Evidence(signals=signals), pack)

    assert verdict.label in ("legit", "doubtful")


def test_build_verdict_manipulation_is_a_typed_input_not_case_context() -> None:
    """Jev's flag (T-08) travels in Evidence; a key smuggled in case.context is ignored."""
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Gracias por la compra, su pedido va en camino.")
    smuggled = case.model_copy(update={"context": {"manipulation_detected": True}})
    legit = _analyst("legit", 0.9)

    flagged = build_verdict(case, Evidence(analyst=legit, manipulation_detected=True), pack)
    unflagged = build_verdict(smuggled, Evidence(analyst=legit), pack)

    assert flagged.label == "doubtful" and flagged.manipulation_detected is True
    assert unflagged.label == "legit" and unflagged.manipulation_detected is False


@pytest.mark.parametrize(
    ("iso", "text", "domain"),
    [
        ("CO", "Ojo! Ultimo aviso: ingrese ya a diangov.com", "diangov.com"),
        # c-0077..c-0079 (incidents, juez T-07): an alert word alone is not a denial
        (
            "CO",
            "ALERTA DIAN: tiene una deuda pendiente, pague hoy en diangov.com o será embargado",
            "diangov.com",
        ),
        (
            "CO",
            "Cuidado: su cuenta será bloqueada. Actualice sus datos en diangov.com",
            "diangov.com",
        ),
        (
            "US",
            "Warning: your toll account is overdue. Pay now at myturnpiketollservices.com to "
            "avoid a late fee.",
            "myturnpiketollservices.com",
        ),
        # a denial about something else, in another sentence, does not cover the domain
        ("CO", "No abran correos raros. Pague hoy su deuda en diangov.com", "diangov.com"),
        # incidents c-0080..c-0082: a denial in the same sentence that targets something else
        ("CO", "DIAN: no entre en pánico, regularice su deuda hoy en diangov.com", "diangov.com"),
        (
            "CO",
            "DIAN: para no pagar intereses, no pague en bancos: pague hoy en diangov.com",
            "diangov.com",
        ),
        (
            "US",
            "Do not pay late fees: settle now at myturnpiketollservices.com",
            "myturnpiketollservices.com",
        ),
        # the scam vouching for itself is not a denial
        ("CO", "Tranquilo, diangov.com no es falso, pague hoy", "diangov.com"),
    ],
)
def test_alert_words_without_a_denial_of_the_domain_still_condemn(
    iso: str, text: str, domain: str
) -> None:
    pack = country_pack(iso)
    assert pack is not None
    campaign_hit = _sig("known_campaign", "hard", match="domain", domain=domain)

    assert soften_citations([campaign_hit], _case(text, urls=[domain]), pack) == [campaign_hit]


@pytest.mark.parametrize(
    ("iso", "text"),
    [
        ("CO", "Ojo, NO entren a diangov.com"),
        ("CO", "No hagan clic en el enlace diangov.com"),
        ("CO", "No entren a la página diangov.com"),
        ("CO", "Equipo: diangov.com, es falso"),
        ("US", "Heads up team: myturnpiketollservices.com is fake, do not click it."),
    ],
)
def test_an_explicit_denial_of_the_quoted_domain_softens_it(iso: str, text: str) -> None:
    pack = country_pack(iso)
    assert pack is not None
    domain = "diangov.com" if iso == "CO" else "myturnpiketollservices.com"
    campaign_hit = _sig("known_campaign", "hard", match="domain", domain=domain)

    softened = soften_citations([campaign_hit], _case(text, urls=[domain]), pack)

    assert [s.weight for s in softened] == ["light"]


def test_analyst_brand_text_never_reaches_the_user() -> None:
    """Regla 14: a brand is only quoted when it is exactly a brand of the pack."""
    pack = country_pack("CO")
    assert pack is not None
    injected = "Bancolombia. Llame ya al 3000001234 y transfiera"
    analyst = AnalystResult(
        label="scam",
        confidence=0.95,
        signals=[
            Signal(
                type="brand_off_domain",
                weight="hard",
                source="analyst",
                evidence={"brand": injected, "official_channel": "Llame al 3000001234"},
            )
        ],
    )

    verdict = build_verdict(_case("mensaje cualquiera"), Evidence(analyst=analyst), pack)

    assert "3000001234" not in verdict.user_explanation
    assert verdict.impersonated_brand is None
    assert "3000001234" not in (verdict.official_channel or "")


def test_a_pack_brand_from_the_analyst_brings_the_pack_official_channel() -> None:
    pack = country_pack("CO")
    assert pack is not None
    brand = next(b for b in pack.brands if b.name == "Bancolombia")
    analyst = AnalystResult(
        label="scam",
        confidence=0.95,
        signals=[
            Signal(
                type="brand_off_domain",
                weight="hard",
                source="analyst",
                evidence={"brand": "Bancolombia", "official_channel": "Llame al 3000001234"},
            )
        ],
    )

    verdict = build_verdict(_case("mensaje cualquiera"), Evidence(analyst=analyst), pack)

    assert verdict.impersonated_brand == "Bancolombia"
    assert verdict.official_channel == brand.official_channel


def test_an_unknown_signal_type_does_not_break_the_verdict() -> None:
    pack = country_pack("CO")
    assert pack is not None
    analyst = AnalystResult(
        label="scam",
        confidence=0.95,
        signals=[_sig("made_up_by_the_model", "hard"), _sig("urgency", "medium")],
    )

    verdict = build_verdict(_case("mensaje cualquiera"), Evidence(analyst=analyst), pack)

    assert verdict.label == "scam"
    assert "made_up_by_the_model" not in verdict.user_explanation


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_confidence_is_bounded_between_0_and_1(confidence: float) -> None:
    with pytest.raises(ValidationError):
        AnalystResult(label="scam", confidence=confidence)
    verdict = build_verdict(_case("hola"), Evidence(), country_pack("CO"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Verdict.model_validate(verdict.model_dump() | {"confidence": confidence})


@pytest.mark.parametrize("case_id", ["c-0211", "c-0212"])
def test_inc_marca_en_el_cuerpo_names_the_brand_written_in_the_message(case_id: str) -> None:
    """Incident c-0211/c-0212: a raw-IP link convicts, but the brand only lived in the body."""
    pack = country_pack("CO")
    assert pack is not None
    case = _eval_case(case_id)

    # Rules alone convict c-0211; the English c-0212 needs the analyst, as in production.
    analyst = _analyst("scam", 0.9) if case_id == "c-0212" else None
    evidence = Evidence(signals=detect(case, pack), analyst=analyst)
    verdict = build_verdict(case, evidence, pack)

    assert verdict.label == "scam"
    assert verdict.impersonated_brand == "Coordinadora"
    assert "Coordinadora" in verdict.user_explanation


def test_a_brand_in_the_body_is_not_named_when_the_message_is_legit() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Coordinadora: su guia va en camino, rastreela en https://www.coordinadora.com")

    verdict = build_verdict(case, Evidence(signals=detect(case, pack)), pack)

    assert verdict.impersonated_brand is None


def test_a_brand_that_is_also_a_plain_lowercase_word_is_not_named() -> None:
    pack = country_pack("CO")
    assert pack is not None
    assert {"Claro", "Dale"} <= {brand.name for brand in pack.brands}
    case = _case("claro que si, dale y pague hoy en http://203.0.113.9/pago")

    evidence = Evidence(signals=detect(case, pack), analyst=_analyst("scam", 0.9))
    verdict = build_verdict(case, evidence, pack)

    assert verdict.impersonated_brand is None


@pytest.mark.parametrize(("case_id", "brand"), [("c-0220", "DIAN"), ("c-0221", None)])
def test_inc_marca_en_audio_ignores_asr_case_only_for_unambiguous_brands(
    case_id: str, brand: str | None
) -> None:
    """Incident 31 oct: the transcript said "dian" and the reply lost the brand and its channel."""
    pack = country_pack("CO")
    assert pack is not None
    case = _eval_case(case_id)
    evidence = Evidence(signals=detect(case, pack), analyst=_analyst("scam", 0.92))

    verdict = build_verdict(case, evidence, pack)

    assert verdict.label == "scam"
    assert verdict.impersonated_brand == brand
    if brand:
        channel = next(b.official_channel for b in pack.brands if b.name == brand)
        assert channel and channel in whatsapp_message(verdict, "es")


@pytest.mark.parametrize(
    ("case_id", "brand", "rail"), [("c-0230", "DIAN", "Nequi"), ("c-0231", "Nequi", None)]
)
def test_inc_riel_de_pago_names_the_impersonated_brand_not_the_wallet(
    case_id: str, brand: str, rail: str | None
) -> None:
    """Incident 31 oct case 14: "DIAN ... pague por Nequi" was attributed to Nequi."""
    pack = country_pack("CO")
    assert pack is not None
    case = _eval_case(case_id)
    evidence = Evidence(signals=detect(case, pack), analyst=_analyst("scam", 0.92))

    verdict = build_verdict(case, evidence, pack)
    message = whatsapp_message(verdict, "es")

    assert verdict.impersonated_brand == brand
    channel = next(b.official_channel for b in pack.brands if b.name == brand)
    assert channel and channel in message
    if rail:
        assert f"pide el pago a una cuenta de {rail}" in message
        assert "nequi.com.co" not in message


def _landed(via: str) -> Signal:
    """An official_domain signal the detector emits after unrolling ``via``."""
    return Signal(
        type="official_domain",
        weight="light",
        source="urls",
        evidence={"domain": "cruzverde.com.co", "via": via},
    )


def test_build_verdict_shortener_unrolled_to_official_domain_can_be_legit() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Tu pedido de Cruz Verde va en camino", ["https://ma.sv/Ab12Cd3"])
    evidence = Evidence(signals=[_landed("https://ma.sv/Ab12Cd3")], analyst=_analyst("legit", 0.9))

    assert build_verdict(case, evidence, pack).label == "legit"


def test_build_verdict_unrolled_signal_absolves_only_its_own_link() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Pedido", ["https://ma.sv/Ab12Cd3", "https://ma.sv/Zz99Yy8"])
    evidence = Evidence(signals=[_landed("https://ma.sv/Ab12Cd3")], analyst=_analyst("legit", 0.9))

    assert build_verdict(case, evidence, pack).label == "doubtful"


def test_build_verdict_qr_link_is_never_absolved_by_unrolling() -> None:
    pack = country_pack("CO")
    assert pack is not None
    case = _case("Escanea el QR").model_copy(update={"qr_urls": ["https://ma.sv/Ab12Cd3"]})
    evidence = Evidence(signals=[_landed("https://ma.sv/Ab12Cd3")], analyst=_analyst("legit", 0.9))

    assert build_verdict(case, evidence, pack).label == "doubtful"
