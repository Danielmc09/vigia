import json
from datetime import date
from pathlib import Path

import measure
import pytest

from vigia.domain.case import Case

REPO = Path(__file__).parents[1]
BASE = {
    "tipo": "texto",
    "pais": "CO",
    "hipotesis": "h",
    "autor": "claude",
    "fecha": "2026-10-27",
    "origen": "sintetico",
}


def _case(case_id: str, expected: str, text: str, **extra: object) -> dict[str, object]:
    category = "legitimo_banco" if expected == "legitimo" else "banco"
    return {
        **BASE,
        "id": case_id,
        "esperado": expected,
        "categoria": category,
        "texto": text,
        **extra,
    }


SCAM_1 = _case("c-1", "estafa", "scam one")
SCAM_2 = _case("c-2", "estafa", "scam two", tipo="audio", pais="US")
LEGIT = _case("c-3", "legitimo", "legit")


def _write(tmp_path: Path, *cases: dict[str, object]) -> Path:
    path = tmp_path / "casos.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    return path


def _by_text(answers: dict[str, str], usd: float = 0.0) -> measure.Classifier:
    """A fake app: answers a fixed label per case text."""

    def classify(case: Case) -> measure.Outcome:
        return measure.Outcome(label=answers[case.text or ""], usd=usd, tokens=10)

    return classify


PERFECT = _by_text({"scam one": "scam", "scam two": "scam", "legit": "legit"})


def _run(tmp_path: Path, classify: measure.Classifier | None, *args: str) -> int:
    path = _write(tmp_path, SCAM_1, SCAM_2, LEGIT)
    return measure.main([str(path), *args], classify=classify, out_dir=tmp_path)


def _result(tmp_path: Path, name: str = f"{date.today()}.resultado.json") -> dict:
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def test_grader_counts_exact_matches_and_scam_metrics(tmp_path: Path) -> None:
    always_scam = _by_text({"scam one": "scam", "scam two": "doubtful", "legit": "scam"})

    _run(tmp_path, always_scam)
    metrics = _result(tmp_path)["metricas"]

    assert metrics["exactitud"] == pytest.approx(1 / 3)
    assert metrics["recall_estafa"] == 0.5
    assert metrics["fp_legitimo"] == 1.0
    assert metrics["dudosos"] == pytest.approx(1 / 3)


def test_metrics_are_broken_down_by_category_type_and_country(tmp_path: Path) -> None:
    _run(tmp_path, _by_text({"scam one": "scam", "scam two": "legit", "legit": "legit"}))
    metrics = _result(tmp_path)["metricas"]

    assert metrics["por_tipo"] == {"texto": 1.0, "audio": 0.0}
    assert metrics["por_pais"] == {"CO": 1.0, "US": 0.0}
    assert metrics["por_categoria"] == {"banco": 0.5, "legitimo_banco": 1.0}


def test_perfect_run_passes_the_gate_and_records_each_case(tmp_path: Path) -> None:
    assert _run(tmp_path, PERFECT) == 0

    first = _result(tmp_path)["casos"][0]
    assert first["id"] == "c-1" and first["esperado"] == "estafa" and first["obtenido"] == "estafa"
    assert first["paso"] is True
    assert _result(tmp_path, "ultimo.resultado.json")["casos"][0] == first


def test_failing_run_does_not_become_the_baseline(tmp_path: Path) -> None:
    assert _run(tmp_path, _by_text(dict.fromkeys(("scam one", "scam two", "legit"), "legit"))) == 1

    assert not (tmp_path / "ultimo.resultado.json").exists()


def test_gate_fails_when_recall_is_below_threshold(tmp_path: Path) -> None:
    missed = _by_text({"scam one": "scam", "scam two": "legit", "legit": "legit"})

    assert _run(tmp_path, missed) == 1


def test_drop_of_more_than_two_points_against_last_run_fails(tmp_path: Path) -> None:
    baseline = {"metricas": {"exactitud": 1.0, "recall_estafa": 1.0, "fp_legitimo": 0.0}}
    (tmp_path / "ultimo.resultado.json").write_text(json.dumps(baseline), encoding="utf-8")
    cases = [SCAM_1, SCAM_2, LEGIT] + [_case(f"s-{i}", "estafa", "scam one") for i in range(20)]
    cases.append(_case("s-x", "estafa", "missed"))
    path = _write(tmp_path, *cases)
    answers = {"scam one": "scam", "scam two": "scam", "legit": "legit", "missed": "legit"}

    code = measure.main([str(path)], classify=_by_text(answers), out_dir=tmp_path)

    assert code == 1
    assert _result(tmp_path)["regresiones"] == ["exactitud", "recall_estafa"]


def test_case_without_attachment_is_graded_from_its_text(tmp_path: Path) -> None:
    seen: list[Case] = []

    def classify(case: Case) -> measure.Outcome:
        seen.append(case)
        return PERFECT(case)

    _run(tmp_path, classify)

    assert [c.attachments for c in seen] == [[], [], []]
    assert [c.kind for c in seen] == ["text", "audio", "text"]
    assert seen[1].country == "US" and seen[1].origin == "eval"


def test_missing_attachment_fails_the_case_without_calling_the_app(tmp_path: Path) -> None:
    image = _case("c-9", "estafa", "scam one", tipo="imagen", fuente=str(tmp_path / "no.png"))
    path = _write(tmp_path, image, SCAM_2, LEGIT)

    measure.main([str(path)], classify=PERFECT, out_dir=tmp_path)
    first = _result(tmp_path)["casos"][0]

    assert first["obtenido"] is None and first["paso"] is False
    assert "attachment" in first["error"]


def test_timeout_fails_the_case_and_the_run_continues(tmp_path: Path) -> None:
    def classify(case: Case) -> measure.Outcome:
        if case.text == "scam one":
            raise TimeoutError
        return PERFECT(case)

    _run(tmp_path, classify)
    cases = _result(tmp_path)["casos"]

    assert cases[0]["error"] == "timeout" and cases[0]["paso"] is False
    assert [c["paso"] for c in cases[1:]] == [True, True]


def test_every_run_records_tokens_and_usd(tmp_path: Path) -> None:
    _run(tmp_path, _by_text(dict.fromkeys(("scam one", "scam two", "legit"), "scam"), usd=0.25))
    metrics = _result(tmp_path)["metricas"]

    assert metrics["usd_total"] == pytest.approx(0.75)
    assert metrics["tokens_total"] == 30


def test_spending_over_the_cap_stops_the_run_with_exit_2(tmp_path: Path) -> None:
    pricey = _by_text(dict.fromkeys(("scam one", "scam two", "legit"), "scam"), usd=1.5)

    assert _run(tmp_path, pricey, "--max-usd", "2.0") == 2
    assert len(_result(tmp_path)["casos"]) == 2


def test_filter_runs_a_subset_and_leaves_the_baseline_alone(tmp_path: Path) -> None:
    out = tmp_path / "audio.json"

    assert _run(tmp_path, PERFECT, "--filtro", "tipo=audio", "--json", str(out)) == 0

    assert [c["id"] for c in json.loads(out.read_text(encoding="utf-8"))["casos"]] == ["c-2"]
    assert not (tmp_path / "ultimo.resultado.json").exists()


def test_deterministic_only_reports_without_gate_on_its_own_baseline(tmp_path: Path) -> None:
    assert _run(tmp_path, None, "--solo-deterministas") == 0

    result = _result(tmp_path, "ultimo.deterministas.resultado.json")
    assert result["modo"] == "deterministas"
    assert result["metricas"]["usd_total"] == 0 and result["metricas"]["tokens_total"] == 0
    assert not (tmp_path / "ultimo.resultado.json").exists()


def test_full_mode_without_an_app_is_a_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, None) == 3
    assert "--solo-deterministas" in capsys.readouterr().err


def test_adversarial_set_missing_is_a_clear_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = measure.main(["--adversarios"], out_dir=tmp_path)

    assert code == 3
    assert "adversarios.jsonl" in capsys.readouterr().err


def test_repository_eval_set_runs_deterministic_end_to_end(tmp_path: Path) -> None:
    code = measure.main(
        [str(REPO / "evals" / "casos.jsonl"), "--solo-deterministas"], out_dir=tmp_path
    )

    result = _result(tmp_path, "ultimo.deterministas.resultado.json")
    assert code == 0
    assert len(result["casos"]) == len((REPO / "evals" / "casos.jsonl").read_text().splitlines())


def test_eval_record_goes_through_the_same_normalization_as_a_channel_message() -> None:
    case = measure.to_case(_case("c-9", "estafa", "https://bit.ly/abc"))
    assert case.kind == "url"
    assert case.origin == "eval"
    assert case.urls == ["https://bit.ly/abc"]


def test_eval_email_record_carries_email_meta() -> None:
    case = measure.to_case(_case("c-9", "estafa", "Cambiamos la cuenta", tipo="correo"))
    assert case.kind == "email"
    assert case.email is not None


def test_eval_record_of_unsupported_type_fails_loudly() -> None:
    with pytest.raises(ValueError, match="c-9"):
        measure.to_case(_case("c-9", "estafa", "x", tipo="pdf"))


def test_each_expected_value_maps_to_its_own_label() -> None:
    from vigia.domain.eval_case import EXPECTED_LABEL

    assert EXPECTED_LABEL == {"estafa": "scam", "legitimo": "legit", "dudoso": "doubtful"}
