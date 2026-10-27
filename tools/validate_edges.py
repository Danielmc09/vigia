"""List the edge cases of specs/12 marked ``T`` that no test covers.

A test declares the rows it covers with ``@pytest.mark.edge("A1", ...)`` (D-036); an
``xfail`` test counts. Usage: ``python tools/validate_edges.py [spec] [tests_dir]``.
Exit 0 when every ``T`` row has a test, 1 otherwise (one line per row on stderr).
"""

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
DEFAULT_SPEC = REPO / "specs" / "12-casos-borde.md"
DEFAULT_TESTS = REPO / "tests"

_ROW = re.compile(r"^\|\s*([A-Z]\d+)\s*\|.*\|\s*([A-Z/]+)\s*\|\s*$", re.M)


def _rows(spec: str) -> dict[str, set[str]]:
    """Every row id of the spec tables with the set of its ``Cubre`` letters."""
    return {m[1]: set(m[2].split("/")) for m in _ROW.finditer(spec)}


def unit_rows(spec: str) -> list[str]:
    """Row ids whose ``Cubre`` column includes ``T``, in spec order."""
    return [row for row, cover in _rows(spec).items() if "T" in cover]


def _marker_ids(source: str) -> set[str]:
    """String arguments of every ``mark.edge(...)`` call (code, not text that mentions it)."""
    return {
        arg.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "edge"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "mark"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }


def covered(tests_dir: Path) -> set[str]:
    """Row ids named by any ``edge`` marker in the test files."""
    return {row for path in tests_dir.rglob("*.py") for row in _marker_ids(path.read_text("utf-8"))}


def missing(spec: str, tests_dir: Path) -> list[str]:
    """``T`` rows with no test."""
    found = covered(tests_dir)
    return [row for row in unit_rows(spec) if row not in found]


def unknown(spec: str, tests_dir: Path) -> list[str]:
    """Marker ids that are not rows of the spec (typos, deleted rows)."""
    return sorted(covered(tests_dir) - _rows(spec).keys())


def main(argv: list[str]) -> int:
    """Check the spec and tests given in ``argv`` (or the defaults) and report."""
    spec_path = Path(argv[0]) if argv else DEFAULT_SPEC
    tests_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_TESTS
    spec = spec_path.read_text("utf-8")
    problems = [f"{row}: no test" for row in missing(spec, tests_dir)]
    problems += [f"{row}: marker names no row of the spec" for row in unknown(spec, tests_dir)]
    for problem in problems:
        print(problem, file=sys.stderr)
    total = len(unit_rows(spec))
    print(f"{spec_path.name}: {total - len(missing(spec, tests_dir))}/{total} T rows with a test")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
