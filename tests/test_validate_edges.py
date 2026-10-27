from pathlib import Path

import validate_edges

SPEC = """\
## A. Entrada
| # | Caso | Comportamiento esperado | Cubre |
|---|---|---|---|
| A1 | Mensaje vacio | Ayuda | T |
| A2 | Texto largo | Se analiza | T/E |
| A7 | Captura | Spark | E |
| C1 | 429 | Reintento | T/C |
"""


def _tests(tmp_path: Path, source: str) -> Path:
    (tmp_path / "test_x.py").write_text(source, encoding="utf-8")
    return tmp_path


def _test(*rows: str) -> str:
    return f"@pytest.mark.edge({', '.join(map(repr, rows))})\ndef test_x(): ...\n"


def test_rows_covered_by_unit_tests_are_parsed_from_the_spec() -> None:
    assert validate_edges.unit_rows(SPEC) == ["A1", "A2", "C1"]


def test_rows_without_an_edge_marker_are_reported(tmp_path: Path) -> None:
    tests = _tests(
        tmp_path,
        '@pytest.mark.edge("A1")\ndef test_a(): ...\n'
        '@pytest.mark.edge("C1", "A2")\n@pytest.mark.xfail(strict=True, reason="T-09")\n'
        "def test_b(): ...\n",
    )

    assert validate_edges.missing(SPEC, tests) == []
    assert validate_edges.missing(SPEC, _tests(tmp_path, _test("A1"))) == [
        "A2",
        "C1",
    ]


def test_markers_naming_rows_that_do_not_exist_are_reported(tmp_path: Path) -> None:
    tests = _tests(tmp_path, _test("A1", "A99") + "x = \"pytest.mark.edge('A2')\"\n")

    assert validate_edges.unknown(SPEC, tests) == ["A99"]
    assert "A2" in validate_edges.missing(SPEC, tests)  # a string that mentions it is not a marker


def test_main_exits_1_while_a_row_has_no_test(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    (tmp_path / "t").mkdir()
    tests = _tests(tmp_path / "t", "")

    assert validate_edges.main([str(spec), str(tests)]) == 1
    (tests / "test_x.py").write_text(_test("A1", "A2", "C1"), encoding="utf-8")
    assert validate_edges.main([str(spec), str(tests)]) == 0
