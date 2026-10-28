"""Deterministic signals of specs/04: URLs, text patterns, address book, QR and the detector."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from vigia.domain.case import Case, EmailMeta
from vigia.domain.country import CountryPack
from vigia.domain.member import Supplier
from vigia.domain.signal import Signal
from vigia.signals.address_book import address_book_signals
from vigia.signals.country_pack import country_pack
from vigia.signals.detector import detect
from vigia.signals.patterns import pattern_signals
from vigia.signals.qr import decode_qr, qr_signals
from vigia.signals.urls import registrable_domain, url_signals

CO = country_pack("CO")
US = country_pack("US")
assert CO is not None and US is not None


def _found(signals: list[Signal]) -> set[tuple[str, str]]:
    return {(s.type, s.weight) for s in signals}


def _case(text: str, country: str = "CO", email: EmailMeta | None = None) -> Case:
    from vigia.domain.urls import extract_urls

    return Case(
        id=uuid4(),
        business_id=uuid4(),
        country=country,
        origin="eval",
        kind="email" if email else "text",
        text=text,
        urls=extract_urls(text),
        email=email,
        received_at=datetime(2026, 10, 27, tzinfo=UTC),
    )


# --- country pack ------------------------------------------------------------------------------


def test_country_pack_is_typed_in_the_domain() -> None:
    assert isinstance(CO, CountryPack)
    assert CO.country == "CO" and CO.language == "es"
    assert "bancolombia.com" in CO.official_domains()


def test_unknown_country_has_no_pack() -> None:
    assert country_pack("ZZ") is None
    assert country_pack("co") is None


# --- registrable domain ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.bancolombia.com", "bancolombia.com"),
        ("www.claro.com.co", "claro.com.co"),
        ("transaccional.dian.gov.co", "dian.gov.co"),
        ("www.fcm.org.co", "fcm.org.co"),
        ("interrapidisimo.co-envios.com", "co-envios.com"),
        ("bancolombia-seguro.co", "bancolombia-seguro.co"),
        ("usps.com:443", "usps.com"),
        ("192.0.2.45:8080", "192.0.2.45"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


# --- URLs --------------------------------------------------------------------------------------

URL_FIRES = [
    # (url, pack, signal type, weight)
    ("https://bancolornbia.com/login", "CO", "lookalike_domain", "hard"),
    ("https://davivlenda.com", "CO", "lookalike_domain", "hard"),
    ("https://dav1vienda.com", "CO", "lookalike_domain", "hard"),
    ("https://servientrega.co", "CO", "lookalike_domain", "hard"),
    ("https://xn--bncolombia-zqi.com", "CO", "lookalike_domain", "hard"),  # Cyrillic a
    ("https://paypa1.com/signin", "US", "lookalike_domain", "hard"),
    ("https://bancolombia-seguro.co/verificar", "CO", "brand_off_domain", "hard"),
    ("servientrega-co.xyz/pago", "CO", "brand_off_domain", "hard"),
    ("https://interrapidisimo.co-envios.com/pago", "CO", "brand_off_domain", "hard"),
    ("prosperidad-social.online", "CO", "brand_off_domain", "hard"),
    ("exito-aniversario.shop", "CO", "brand_off_domain", "hard"),
    ("simit-pagos.click", "CO", "brand_off_domain", "hard"),
    ("https://pagos-seguros.example/nequi/login", "CO", "brand_in_path", "medium"),
    ("https://evil.example/bancolombia/login", "CO", "brand_in_path", "medium"),
    ("usps-redelivery.top/track", "US", "brand_off_domain", "hard"),
    ("irs-refund-gov.top", "US", "brand_off_domain", "hard"),
    ("claro-puntos.xyz", "CO", "suspicious_tld", "medium"),
    ("https://ayuda-vigia.click", "CO", "suspicious_tld", "medium"),
    ("premios.icu/r", "CO", "suspicious_tld", "medium"),
    ("http://192.0.2.45:8080/bogota", "CO", "ip_or_port", "medium"),
    ("https://ejemplo.example:8443/x", "CO", "ip_or_port", "medium"),
    ("https://bit.ly/3fGn7Qx", "CO", "unresolved_shortener", "medium"),
    ("cutt.ly/abc", "CO", "unresolved_shortener", "medium"),
    ("http://ejemplo.example", "CO", "http_without_tls", "light"),
    ("https://www.servientrega.com/rastreo", "CO", "official_domain", "light"),
    ("https://www.claro.com.co/personas/", "CO", "official_domain", "light"),
    ("https://www.usps.com", "US", "official_domain", "light"),
    ("https://paypal.me/juan", "US", "official_domain", "light"),
]


@pytest.mark.parametrize(("url", "iso", "kind", "weight"), URL_FIRES)
def test_url_signal_fires(url: str, iso: str, kind: str, weight: str) -> None:
    pack = country_pack(iso)
    assert pack is not None
    assert (kind, weight) in _found(url_signals(url, pack))


URL_SILENT = [
    # (url, pack, signal type that must NOT fire)
    ("https://www.bancolombia.com/personas", "CO", "lookalike_domain"),
    ("https://www.bancolombia.com/personas", "CO", "brand_off_domain"),
    ("https://www.mercadolibre.com.co", "CO", "brand_off_domain"),
    ("https://www.rappipay.co", "CO", "brand_off_domain"),  # rappi inside another brand's domain
    ("https://www.dian.gov.co", "CO", "suspicious_tld"),
    ("https://groups.example.com", "US", "brand_off_domain"),  # "ups" is a short alias: token only
    (
        "https://apps.com",
        "US",
        "lookalike_domain",
    ),  # 2 edits from ups.com: short names need homographs
    ("https://bancolombia-seguro.co", "CO", "official_domain"),
    ("https://www.usps.com", "US", "http_without_tls"),
    # Incidents c-0048..c-0050: a brand word only in the path never condemns alone.
    ("https://www.tiendaropa.com.co/checkout/pse", "CO", "brand_off_domain"),
    ("https://www.payu.com.co/pse", "CO", "brand_off_domain"),
    ("https://www.colegio.edu.co/pagos/pse/", "CO", "brand_off_domain"),
    ("https://www.tienda-moda.example/promo/dale-click", "CO", "brand_off_domain"),
    ("https://www.tienda-moda.example/movil/claro", "CO", "brand_off_domain"),
    ("https://paypal.me/juan", "US", "brand_off_domain"),
]


@pytest.mark.parametrize(("url", "iso", "kind"), URL_SILENT)
def test_url_signal_stays_silent(url: str, iso: str, kind: str) -> None:
    pack = country_pack(iso)
    assert pack is not None
    assert kind not in {s.type for s in url_signals(url, pack)}


def test_url_signals_carry_url_domain_and_origin_as_evidence() -> None:
    signal = next(s for s in url_signals("claro-puntos.xyz", CO) if s.type == "brand_off_domain")
    assert signal.source == "urls"
    assert signal.evidence == {
        "url": "claro-puntos.xyz",
        "domain": "claro-puntos.xyz",
        "origin": "text",
        "brand": "Claro",
    }


def test_resolved_shortener_is_judged_by_its_destination() -> None:
    def resolver(url: str) -> str:
        return "https://bancolombia-seguro.co/x"

    found = _found(url_signals("bit.ly/abc", CO, resolver=resolver))
    assert ("brand_off_domain", "hard") in found
    assert ("unresolved_shortener", "medium") not in found


def test_shortener_to_official_domain_is_official() -> None:
    found = _found(url_signals("bit.ly/abc", CO, resolver=lambda u: "https://www.dian.gov.co"))
    assert found == {("official_domain", "light")}


def test_resolver_failure_is_a_light_signal_not_an_exception() -> None:
    def resolver(url: str) -> str:
        raise TimeoutError

    found = _found(url_signals("bit.ly/abc", CO, resolver=resolver))
    assert ("could_not_verify", "light") in found
    assert ("unresolved_shortener", "medium") in found


def test_resolver_is_not_called_for_regular_links() -> None:
    def resolver(url: str) -> str:
        raise AssertionError("no network for non-shorteners")

    url_signals("https://www.bancolombia.com", CO, resolver=resolver)


# --- text patterns -----------------------------------------------------------------------------

PATTERN_CASES = [
    # (text, pack, unofficial link present, type, weight)
    ("Ingrese su usuario y clave", "CO", True, "credentials_request", "hard"),
    ("Dicte el código de verificación", "CO", True, "credentials_request", "hard"),
    ("Verify your password now", "US", True, "credentials_request", "hard"),
    ("Tu clave dinamica es 482913", "CO", False, "credentials_request", "light"),
    ("Consigna $12.000 al Nequi para liberarlo", "CO", False, "personal_payment_release", "hard"),
    ("Para evitar embargo consigne hoy a Nequi", "CO", False, "personal_payment_release", "hard"),
    ("Send $200 by Zelle to release your package", "US", False, "personal_payment_release", "hard"),
    ("Recibiste $120.000 en Nequi", "CO", False, "personal_payment", "light"),
    ("Su cuenta será bloqueada", "CO", False, "urgency", "medium"),
    ("Su linea sera suspendida en las proximas 24 horas", "CO", False, "urgency", "medium"),
    ("Your account will be locked", "US", False, "urgency", "medium"),
    ("Ganaste un bono de $300.000", "CO", False, "prize", "medium"),
    ("You have won a gift card", "US", False, "prize", "medium"),
    ("DIAN: tiene una sanción pendiente", "CO", True, "authority_with_link", "medium"),
    ("Hola mami, cambie de numero", "CO", False, "family", "medium"),
    ("Mom, this is my new number", "US", False, "family", "medium"),
]


@pytest.mark.parametrize(
    ("text", "iso", "link", "expected"), [(*c[:3], c[3:]) for c in PATTERN_CASES]
)
def test_pattern_signal(text: str, iso: str, link: bool, expected: tuple[str, str]) -> None:
    pack = country_pack(iso)
    assert pack is not None
    assert expected in _found(pattern_signals(text, pack, unofficial_link=link))


PATTERN_SILENT = [
    # (text, pack, unofficial link present, type that must NOT fire)
    ("DIAN: consulte el calendario", "CO", False, "authority_with_link"),
    ("Le abono la factura mañana", "CO", False, "prize"),  # "bono" only as a whole word
    ("Mira mi pinterest", "US", False, "credentials_request"),  # "PIN" only as a whole word
    ("Recibiste $120.000 en Nequi", "CO", False, "personal_payment_release"),
]


@pytest.mark.parametrize(("text", "iso", "link", "kind"), PATTERN_SILENT)
def test_pattern_signal_stays_silent(text: str, iso: str, link: bool, kind: str) -> None:
    pack = country_pack(iso)
    assert pack is not None
    assert kind not in {s.type for s in pattern_signals(text, pack, unofficial_link=link)}


def test_pattern_evidence_names_the_matched_text() -> None:
    signal = next(iter(pattern_signals("Su cuenta SERÁ BLOQUEADA hoy", CO, unofficial_link=False)))
    assert signal.source == "patterns"
    assert signal.evidence == {"match": "SERÁ BLOQUEADA"}


# --- address book ------------------------------------------------------------------------------

ACEROS = Supplier(
    name="Aceros del Norte", email="pagos@acerosnorte.example", account="123-456789-01"
)


def test_supplier_name_matches_whole_words_only() -> None:
    # D-031: "Ana" must not match inside "semana".
    ana = Supplier(name="Ana", email="ana@proveedora.example")
    meta = EmailMeta(sender="otra@gmail.com", display_name="Pagos", with_headers=True)
    assert address_book_signals(_case("Pague esta semana", email=meta), [ana], CO) == []
    hit = address_book_signals(_case("Soy Ana, pague aqui", email=meta), [ana], CO)
    assert ("supplier_from_unknown_origin", "hard") in _found(hit)


@pytest.mark.parametrize(("text", "folded"), [("Código Ñandú", "Codigo Nandu"), ("ok", "ok")])
def test_fold_drops_accents_and_keeps_offsets(text: str, folded: str) -> None:
    from vigia.domain.text import fold

    assert fold(text) == folded and len(fold(text)) == len(text)


def test_supplier_from_unknown_address_is_hard() -> None:
    meta = EmailMeta(
        sender="gerencia.aceros@gmail.com", display_name="Aceros del Norte", with_headers=True
    )
    found = _found(address_book_signals(_case("Adjunto factura", email=meta), [ACEROS], CO))
    assert ("supplier_from_unknown_origin", "hard") in found


def test_supplier_from_its_known_address_is_silent() -> None:
    meta = EmailMeta(
        sender="pagos@acerosnorte.example", display_name="Aceros del Norte", with_headers=True
    )
    assert address_book_signals(_case("Adjunto factura", email=meta), [ACEROS], CO) == []


def test_supplier_from_another_mailbox_of_its_domain_is_silent() -> None:
    meta = EmailMeta(
        sender="cartera@acerosnorte.example", display_name="Aceros del Norte", with_headers=True
    )
    assert address_book_signals(_case("Adjunto factura", email=meta), [ACEROS], CO) == []


def test_account_different_from_the_known_one_is_hard() -> None:
    text = "Aceros del Norte: por auditoria consignar a la cuenta 987-654321-00"
    found = _found(address_book_signals(_case(text), [ACEROS], CO))
    assert ("account_differs_from_known", "hard") in found


def test_known_account_is_silent() -> None:
    text = "Aceros del Norte: el pago a la cuenta 123 456789 01 de siempre"
    assert address_book_signals(_case(text), [ACEROS], CO) == []


def test_unknown_supplier_or_empty_book_is_silent() -> None:
    text = "Ferreteria Sur: consignar a la cuenta 987-654321-00"
    assert address_book_signals(_case(text), [ACEROS], CO) == []
    assert address_book_signals(_case(text), [], CO) == []


def test_phone_number_is_not_an_account() -> None:
    text = "Aceros del Norte: cualquier duda me llama al 300 123 4567"
    assert address_book_signals(_case(text), [ACEROS], CO) == []


def test_supplier_on_free_mail_is_known_only_by_its_exact_address() -> None:
    gmail = Supplier(name="Aceros del Norte", email="acerosnorte@gmail.com")
    meta = EmailMeta(sender="otro@gmail.com", display_name="Aceros del Norte", with_headers=True)
    found = _found(address_book_signals(_case("Adjunto factura", email=meta), [gmail], CO))
    assert ("supplier_from_unknown_origin", "hard") in found


def test_every_pack_pattern_compiles() -> None:
    import re

    from vigia.domain.text import fold

    for pack in (CO, US):
        for patterns in pack.patterns.values():
            for pattern in patterns:
                re.compile(fold(pattern))


# --- QR ----------------------------------------------------------------------------------------


def test_qr_urls_go_through_url_signals_with_qr_origin() -> None:
    signals = qr_signals(b"png", CO, decode=lambda image: ["https://bancolombia-seguro.co"])
    found = _found(signals)
    assert ("brand_off_domain", "hard") in found
    assert ("qr_to_unofficial_domain", "medium") in found
    assert {s.evidence.get("origin") for s in signals if s.source == "urls"} == {"qr"}


def test_qr_to_official_domain_is_not_suspicious() -> None:
    signals = qr_signals(b"png", CO, decode=lambda image: ["https://www.bancolombia.com"])
    assert _found(signals) == {("official_domain", "light")}


@pytest.mark.xfail(
    strict=True,
    reason="No QR decoder is installed and adding one was denied (D-033); arrives with T-12.",
)
def test_decode_qr_reads_an_image() -> None:
    assert decode_qr(b"\x89PNG qr of https://example.com") == ["https://example.com"]


# --- detector ----------------------------------------------------------------------------------


def test_detector_combines_sources_sorted_by_weight() -> None:
    text = "Bancolombia: su cuenta sera bloqueada. Verifique en https://bancolombia-seguro.co"
    signals = detect(_case(text), CO)
    weights = [s.weight for s in signals]
    assert weights == sorted(weights, key=["hard", "medium", "light"].index)
    assert ("brand_off_domain", "hard") in _found(signals)
    assert ("urgency", "medium") in _found(signals)


def test_detector_feeds_unofficial_links_to_patterns() -> None:
    signals = detect(_case("Envie su clave a ayuda.click"), CO)
    assert ("credentials_request", "hard") in _found(signals)


def test_detector_official_link_keeps_credentials_light() -> None:
    signals = detect(_case("Cambie su clave en https://www.bancolombia.com"), CO)
    assert ("credentials_request", "light") in _found(signals)


def test_detector_uses_the_address_book() -> None:
    text = "Aceros del Norte: nueva cuenta 987-654321-00"
    assert ("account_differs_from_known", "hard") in _found(detect(_case(text), CO, [ACEROS]))


def test_detector_never_raises() -> None:
    def resolver(url: str) -> str:
        raise RuntimeError("boom")

    signals = detect(_case("bit.ly/x"), CO, resolver=resolver)
    assert ("could_not_verify", "light") in _found(signals)


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("Link de cobro FV-2291: https://pagos.pasarela-cobros.example/checkout/pse/fv-2291", "CO"),
        ("Mira los vestidos en https://www.tienda-moda.example/promo/dale-click-color-claro", "CO"),
        ("Send me your share here: https://paypal.me/jordanexample", "US"),
    ],
)
def test_incident_legit_links_are_not_scam(text: str, iso: str) -> None:
    from vigia.verdict.fusion import fuse

    pack = country_pack(iso)
    assert pack is not None
    label, _ = fuse(detect(_case(text, iso), pack))
    assert label != "scam"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+57 300-000", "57300000"), ("cta 12 34", "1234"), ("٣٠٠ 12", "12"), ("sin", "")],
)
def test_only_digits_keeps_ascii_digits(raw: str, expected: str) -> None:
    """Non-ASCII digits (Arabic-Indic) are dropped: phones and accounts are ASCII only."""
    from vigia.domain.text import only_digits

    assert only_digits(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Banco de Bogotá", "bancodebogota"), ("Nequi-Pagos.co", "nequipagosco"), ("ÑU", "nu")],
)
def test_squash_folds_and_keeps_alnum(text: str, expected: str) -> None:
    """Names compare to hosts after folding accents and dropping everything but a-z0-9."""
    from vigia.domain.text import squash

    assert squash(text) == expected
