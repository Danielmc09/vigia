"""Known-campaign knowledge base of specs/10 §4: fingerprints, the hard signal, table, hygiene."""

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import validate_cases

from vigia.domain.case import Case
from vigia.domain.knowledge import hamming, phash, simhash
from vigia.domain.urls import extract_urls
from vigia.signals.country_pack import country_pack
from vigia.signals.detector import detect
from vigia.signals.knowledge import knowledge_signals, load_campaigns, with_campaigns
from vigia.store.knowledge import fetch_known_campaigns, insert_known_campaigns
from vigia.verdict.fusion import fuse

REPO = Path(__file__).parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "knowledge" / "campaigns.jsonl"
SEED = REPO / "conocimiento" / "semilla.jsonl"
CAMPAIGNS = load_campaigns(FIXTURE)
_CO, _US = country_pack("CO"), country_pack("US")
assert _CO is not None and _US is not None
CO, US = with_campaigns(_CO, CAMPAIGNS), with_campaigns(_US, CAMPAIGNS)

DIAN = (
    "DIAN le informa que tiene un embargo pendiente por deuda tributaria, regularice su "
    "situacion hoy mismo ingresando al siguiente enlace para evitar sanciones"
)


def _case(text: str, country: str = "CO") -> Case:
    return Case(
        id=uuid4(),
        business_id=uuid4(),
        country=country,
        origin="eval",
        kind="text",
        text=text,
        urls=extract_urls(text),
        received_at=datetime(2026, 10, 27, tzinfo=UTC),
    )


def _image(seed: int, brightness: float = 0.0) -> list[list[float]]:
    """A 32x32 grayscale image with texture, from a fixed seed."""
    rng = random.Random(seed)  # noqa: S311 - test data, not crypto
    return [[rng.uniform(0, 200) + brightness for _ in range(32)] for _ in range(32)]


# --- fingerprints -------------------------------------------------------------------------


def test_simhash_ignores_numbers_urls_case_and_accents() -> None:
    variant = (
        "DIAN LE INFORMA que tiene un embargo pendiente $1.250.000 por deuda tributaria, "
        "regularice su situación hoy mismo ingresando al siguiente enlace "
        "dian-pagos.example/x9 para evitar sanciones 2026"
    )

    assert simhash(variant) == simhash(DIAN)


def test_simhash_of_unrelated_texts_is_far() -> None:
    other = (
        "Hola Carlos, te confirmo el pedido de cemento para el martes en la ferreteria, "
        "llevo la factura y el comprobante del pago anterior"
    )

    a, b = simhash(DIAN), simhash(other)
    assert a is not None and b is not None
    assert hamming(a, b) > 6


def test_simhash_needs_enough_words() -> None:
    assert simhash("Hola, ¿cómo estás?") is None
    assert simhash("https://bit.ly/abc 300 000") is None


def test_hamming_counts_differing_bits() -> None:
    assert hamming(0b1011, 0b0001) == 2
    assert hamming(2**64 - 1, 0) == 64


def test_phash_survives_brightness_and_tells_images_apart() -> None:
    original, brighter, other = _image(1), _image(1, brightness=30), _image(2)

    assert hamming(phash(original), phash(brighter)) <= 10
    assert hamming(phash(original), phash(other)) > 10


def test_phash_rejects_a_grid_that_is_not_32_by_32() -> None:
    with pytest.raises(ValueError):
        phash([[0.0] * 8] * 8)


@pytest.mark.xfail(strict=True, raises=ImportError, reason="image decoder arrives in T-12 (D-033)")
def test_phash_of_image_bytes() -> None:
    from vigia.domain.knowledge import image_phash  # type: ignore[attr-defined]

    assert image_phash(b"\x89PNG\r\n\x1a\n") >= 0


# --- loader -------------------------------------------------------------------------------


def test_loader_computes_fingerprints_from_the_seed_format() -> None:
    by_id = {c.id: c for c in CAMPAIGNS}

    assert set(by_id) == {"kb-co-901", "kb-co-902", "kb-co-903", "kb-us-901"}
    assert by_id["kb-co-901"].simhash == simhash(DIAN)
    assert by_id["kb-co-902"].simhash is None  # domain entries are matched by domain only
    assert by_id["kb-co-903"].domains == ("guia-reenvio.example",)
    assert by_id["kb-co-901"].source_url == "https://alertas.example/dian-embargo"


def test_loader_without_a_seed_file_is_empty(tmp_path: Path) -> None:
    assert load_campaigns(tmp_path / "missing.jsonl") == ()


def test_with_campaigns_keeps_only_the_pack_country() -> None:
    assert {c.country for c in CO.campaigns} == {"CO"}
    assert {c.id for c in US.campaigns} == {"kb-us-901"}


# --- signal -------------------------------------------------------------------------------


def test_text_variant_of_a_known_campaign_is_hard_with_its_source() -> None:
    signals = knowledge_signals(_case(DIAN.replace("hoy mismo", "hoy 27")), CO)

    assert [(s.type, s.weight, s.source) for s in signals] == [
        ("known_campaign", "hard", "knowledge")
    ]
    evidence = signals[0].evidence
    assert evidence["campaign"] == "kb-co-901"
    assert evidence["source_url"] == "https://alertas.example/dian-embargo"
    assert evidence["match"] == "text"
    assert evidence["distance"] <= 6


def test_known_domain_matches_exactly_on_host_or_registrable_domain() -> None:
    for text in ("Ingrese a sucursal-personas-bc.example", "Entre a www.guia-reenvio.example/x"):
        signals = knowledge_signals(_case(text), CO)
        assert [s.evidence["match"] for s in signals] == ["domain"], text


def test_similar_domain_is_not_an_exact_match() -> None:
    assert knowledge_signals(_case("Ingrese a sucursal-personas-bc2.example/login"), CO) == []


def test_official_domains_and_shorteners_in_the_base_never_match() -> None:
    text = "Su clave nueva en https://www.bancolombia.com/personas o bit.ly/3xQnq"

    assert knowledge_signals(_case(text), CO) == []


def test_campaign_of_another_country_does_not_fire() -> None:
    usps = next(c for c in CAMPAIGNS if c.id == "kb-us-901")
    text = (
        "USPS your package could not be delivered due to an incomplete address, update "
        "your details within twelve hours or it will be returned"
    )
    assert simhash(text) == usps.simhash

    assert knowledge_signals(_case(text, "CO"), CO) == []
    assert len(knowledge_signals(_case(text, "US"), US)) == 1


def test_known_image_matches_by_phash() -> None:
    known = CAMPAIGNS[0].model_copy(update={"phash": phash(_image(1)), "simhash": None})
    pack = CO.model_copy(update={"campaigns": (known,)})
    images = [phash(_image(2)), phash(_image(1, brightness=30))]

    signals = knowledge_signals(_case("mire esta imagen"), pack, image_hashes=images)

    assert [s.evidence["match"] for s in signals] == ["image"]


def test_detector_runs_the_knowledge_base_and_fusion_says_scam() -> None:
    signals = detect(_case(DIAN), CO)

    assert "known_campaign" in {s.type for s in signals}
    assert fuse(signals)[0] == "scam"


def test_detector_without_campaigns_does_not_fire() -> None:
    assert "known_campaign" not in {s.type for s in detect(_case(DIAN), _CO)}


# --- hygiene of the eval set --------------------------------------------------------------

CASE = {
    "id": "c-1",
    "tipo": "texto",
    "pais": "CO",
    "texto": DIAN.replace("hoy mismo", "hoy"),
    "esperado": "estafa",
    "categoria": "estado",
    "marca": "DIAN",
    "hipotesis": "known campaign (hard)",
    "ambiguo": None,
    "autor": "claude",
    "fecha": "2026-10-27",
    "origen": "sintetico",
}
LEGIT = {
    **CASE,
    "id": "c-2",
    "texto": "Su pedido llegó bien, gracias por comprar con nosotros otra vez este mes",
    "esperado": "legitimo",
    "categoria": "legitimo_otro",
}


def _errors(tmp_path: Path, *cases: dict) -> list[str]:
    path = tmp_path / "casos.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    return validate_cases.validate_file(path, root=tmp_path, knowledge=FIXTURE)


def test_eval_that_collides_with_the_base_is_rejected(tmp_path: Path) -> None:
    errors = _errors(tmp_path, CASE, LEGIT)

    assert len(errors) == 1
    assert "c-1" in errors[0] and "kb-co-901" in errors[0]


def test_kb_test_case_may_collide(tmp_path: Path) -> None:
    assert _errors(tmp_path, {**CASE, "kb_test": True}, LEGIT) == []


def test_kb_test_must_be_a_boolean(tmp_path: Path) -> None:
    errors = _errors(tmp_path, {**LEGIT, "id": "c-3", "kb_test": "yes"}, LEGIT)

    assert errors == ["c-3: invalid kb_test='yes'"]


def _kb_cases() -> list[dict]:
    lines = (REPO / "evals" / "casos.jsonl").read_text("utf-8").splitlines()
    return [c for c in map(json.loads, lines) if c.get("kb_test")]


@pytest.mark.xfail(
    not SEED.is_file(), strict=True, reason="kb_test cases come from conocimiento/semilla.jsonl"
)
def test_three_kb_test_cases_fire_the_hard_signal_from_the_seed() -> None:
    campaigns = load_campaigns(SEED)
    cases = _kb_cases()

    assert len(cases) >= 3
    for raw in cases:
        pack = country_pack(raw["pais"])
        assert pack is not None
        signals = detect(_case(raw["texto"], raw["pais"]), with_campaigns(pack, campaigns))
        assert "known_campaign" in {s.type for s in signals}, raw["id"]
        assert fuse(signals)[0] == "scam", raw["id"]


# --- table --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_table_is_global_and_read_only_for_the_app(
    admin_conn: asyncpg.Connection, app_conn: asyncpg.Connection
) -> None:
    columns = await admin_conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'known_campaigns'"
    )
    assert columns and "business_id" not in {r["column_name"] for r in columns}

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await insert_known_campaigns(app_conn, CAMPAIGNS[:1])


@pytest.mark.anyio
async def test_app_reads_the_campaigns_the_owner_seeded(admin_dsn: str) -> None:
    from vigia.config import Settings

    owner = await asyncpg.connect(admin_dsn)
    app = await asyncpg.connect(Settings().database_url)
    ids = [c.id for c in CAMPAIGNS]
    try:
        await insert_known_campaigns(owner, CAMPAIGNS)
        read = await fetch_known_campaigns(app, "CO")
    finally:
        await owner.execute("DELETE FROM known_campaigns WHERE id = ANY($1)", ids)
        await owner.close()
        await app.close()

    assert {c for c in read if c.id in ids} == {c for c in CAMPAIGNS if c.country == "CO"}
