import json
from pathlib import Path

import check_public_docs

ROOT = Path(__file__).parents[1]
METRICS = {
    "casos": 221,
    "exactitud": 0.9366,
    "recall_estafa": 0.9918,
    "fp_legitimo": 0.0210,
    "dudosos": 0.0633,
    "usd_total": 0.72932,
    "mediana_ms": 2758.7,
}


def test_latest_result_is_the_newest_full_run_by_date(tmp_path: Path) -> None:
    for name in ("2026-10-30", "2026-10-31", "2026-11-01.deterministas", "ultimo"):
        (tmp_path / f"{name}.resultado.json").write_text(
            json.dumps({"fecha": name, "metricas": METRICS}), encoding="utf-8"
        )

    assert check_public_docs.latest_result(tmp_path).name == "2026-10-31.resultado.json"


def test_metrics_row_carries_every_figure_with_the_given_decimal_mark() -> None:
    row = check_public_docs.metrics_row("2026-10-31", METRICS, ",")

    assert row == "| 2026-10-31 | 221 | 93,7% | 99,2% | 2,1% | 6,3% | $0,729 | 2,76 s |"


def test_links_to_git_ignored_or_missing_paths_are_dead() -> None:
    text = "[a](../ESTADO.md) [b](MAP.md#x) [c](https://example.com) [d](nope.md) [e](#top)"

    dead = check_public_docs.dead_links(text, ROOT / "docs")

    assert dead == ["../ESTADO.md", "nope.md"]


def _case(degraded: bool, tools: list[str], usd: float, ms: float) -> dict[str, object]:
    return {"degradado": degraded, "herramientas_usadas": tools, "usd": usd, "ms": ms}


CASES = [
    _case(True, [], 0.0014, 1300.0),  # rules were conclusive (D-061)
    _case(True, ["read_pdf"], 0.004, 3000.0),  # a model read the attachment, then rules decided
    _case(False, ["web_search"], 0.0038, 3600.0),  # the analyst ran
]


def test_routing_fragments_split_rule_decided_reader_only_and_analyst_cases() -> None:
    fragments = check_public_docs.routing_fragments(CASES, ",")

    assert fragments == [
        "En 1 de los 3 casos",
        "mediana de $0,0014 y 1,3 s por caso, contra $0,0038 y 3,6 s con el analista",
        "En otros 1,",
        "El analista corrió en los 1 restantes.",
    ]


def test_routing_fragments_in_english_use_a_decimal_point() -> None:
    fragments = check_public_docs.routing_fragments(CASES, ".")

    assert fragments[0] == "In 1 of the 3 cases"
    assert fragments[-1] == "The analyst ran in the other 1."
