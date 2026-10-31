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
