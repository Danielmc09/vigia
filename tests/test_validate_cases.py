import json
from pathlib import Path
from typing import get_args

import pytest
import validate_cases

from vigia.domain.eval_case import Category, EvalKind, EvalOrigin, Expected

REPO = Path(__file__).parents[1]
SCAM = {
    "id": "c-1",
    "tipo": "texto",
    "pais": "CO",
    "texto": "Bancolombia: clave en bancolombia-seguro.co",
    "esperado": "estafa",
    "categoria": "banco",
    "marca": "Bancolombia",
    "hipotesis": "lookalike domain (hard)",
    "ambiguo": None,
    "autor": "claude",
    "fecha": "2026-10-27",
    "origen": "sintetico",
}
LEGIT = {**SCAM, "id": "c-2", "esperado": "legitimo", "categoria": "legitimo_banco"}


def _errors(tmp_path: Path, *cases: dict, raw: str = "") -> list[str]:
    path = tmp_path / "casos.jsonl"
    path.write_text(raw + "".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    return validate_cases.validate_file(path, root=tmp_path)


def test_valid_set_has_no_errors(tmp_path: Path) -> None:
    assert _errors(tmp_path, SCAM, LEGIT) == []


@pytest.mark.parametrize(
    "change",
    [
        {"hipotesis": None},
        {"hipotesis": "   "},
        {"autor": None},
        {"texto": None},
        {"tipo": "fax"},
        {"pais": "XX"},
        {"esperado": "maybe"},
        {"categoria": "otra"},
        {"origen": "inventado"},
        {"fecha": "27/10/2026"},
        {"marca": "Banco Inventado"},
        {"esperado": "dudoso"},
        {"fuente": "evals/muestras/missing.png", "texto": None},
    ],
    ids=lambda c: next(iter(c)) + "=" + str(next(iter(c.values()))),
)
def test_invalid_case_is_reported(tmp_path: Path, change: dict) -> None:
    errors = _errors(tmp_path, {**SCAM, **change}, LEGIT)

    assert len(errors) == 1 and errors[0].startswith("c-1:")


def test_online_store_account_scam_has_its_own_category(tmp_path: Path) -> None:
    store = {**SCAM, "pais": "US", "categoria": "comercio", "marca": "Amazon"}

    assert _errors(tmp_path, store, LEGIT) == []


def test_dudoso_is_allowed_when_the_case_is_ambiguous(tmp_path: Path) -> None:
    unclear = {**SCAM, "esperado": "dudoso", "ambiguo": "spec does not decide"}

    assert _errors(tmp_path, unclear, LEGIT) == []


def test_legit_category_must_expect_legitimo(tmp_path: Path) -> None:
    errors = _errors(tmp_path, {**SCAM, "categoria": "legitimo_banco"}, LEGIT)

    assert errors == ["c-1: categoria legitimo_banco requires esperado=legitimo"]


def test_existing_attachment_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"x")

    assert _errors(tmp_path, {**SCAM, "fuente": "a.png", "texto": None}, LEGIT) == []


def test_duplicate_ids_are_reported(tmp_path: Path) -> None:
    assert _errors(tmp_path, SCAM, LEGIT, {**LEGIT, "id": "c-1"}) == ["c-1: duplicate id"]


def test_broken_json_reports_the_line(tmp_path: Path) -> None:
    assert _errors(tmp_path, SCAM, LEGIT, raw="{not json\n")[0].startswith("line 1:")


def test_too_few_legitimate_cases_is_reported(tmp_path: Path) -> None:
    errors = _errors(tmp_path, SCAM, {**SCAM, "id": "c-3"}, LEGIT)

    assert errors == ["legitimate share 33% < 35% (specs/06)"]


def test_cli_exit_code(tmp_path: Path) -> None:
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    good.write_text(json.dumps(SCAM) + "\n" + json.dumps(LEGIT) + "\n", encoding="utf-8")
    bad.write_text(json.dumps(SCAM) + "\n", encoding="utf-8")

    assert validate_cases.main([str(good)]) == 0
    assert validate_cases.main([str(bad)]) == 1


def test_repository_eval_set_is_valid() -> None:
    cases = (REPO / "evals" / "casos.jsonl").read_text(encoding="utf-8").splitlines()

    assert validate_cases.validate_file(REPO / "evals" / "casos.jsonl") == []
    assert len(cases) >= 40
    assert sum(json.loads(c)["esperado"] == "legitimo" for c in cases) >= 15


@pytest.mark.parametrize(("iso", "minimum"), [("CO", 40), ("US", 15)])
def test_country_pack_lists_sourced_brands(iso: str, minimum: int) -> None:
    pack = json.loads((validate_cases.COUNTRIES / f"{iso}.json").read_text(encoding="utf-8"))

    assert pack["pais"] == iso
    assert len(pack["marcas"]) >= minimum
    for brand in pack["marcas"]:
        assert brand["dominios"] and brand["fuente_url"].startswith("https://"), brand["marca"]


def test_vocabulary_comes_from_the_domain() -> None:
    assert validate_cases.CHOICES["categoria"] == set(get_args(Category))
    assert validate_cases.CHOICES["tipo"] == set(get_args(EvalKind))
    assert validate_cases.CHOICES["esperado"] == set(get_args(Expected))
    assert validate_cases.CHOICES["origen"] == set(get_args(EvalOrigin))
