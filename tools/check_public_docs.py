"""Fail if the README metrics row is stale or a public doc links to an ignored/missing path."""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
RUN = re.compile(r"\d{4}-\d{2}-\d{2}\.resultado\.json")  # full runs only, not deterministas
LINK = re.compile(r"\]\(([^)\s]+)\)")
READMES = {"README.md": ".", "README.es.md": ","}


def latest_result(evals: Path) -> Path:
    """The newest dated full-run result in ``evals`` (ISO names sort by date)."""
    return max(p for p in evals.iterdir() if RUN.fullmatch(p.name))


def metrics_row(date: str, m: dict[str, Any], mark: str) -> str:
    """The README metrics row for one run, with ``mark`` as decimal separator."""
    cells = [
        date,
        str(m["casos"]),
        *(f"{m[k] * 100:.1f}%" for k in ("exactitud", "recall_estafa", "fp_legitimo", "dudosos")),
        f"${m['usd_total']:.3f}",
        f"{m['mediana_ms'] / 1000:.2f} s",
    ]
    return "| " + " | ".join(c.replace(".", mark) for c in cells) + " |"


def dead_links(text: str, base: Path) -> list[str]:
    """Relative link targets in ``text`` that are missing or git-ignored, seen from ``base``."""
    targets = [t for t in LINK.findall(text) if not re.match(r"[a-z]+:|#", t)]
    dead = []
    for target in targets:
        path = (base / target.split("#")[0]).resolve()
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(path)])  # noqa: S603, S607
        if not path.exists() or ignored.returncode == 0:
            dead.append(target)
    return dead


def main() -> int:
    """Check the public docs against the latest eval run and git's ignore rules."""
    errors = []
    result = latest_result(ROOT / "evals")
    run = json.loads(result.read_text(encoding="utf-8"))
    for name, mark in READMES.items():
        row = metrics_row(run["fecha"], run["metricas"], mark)
        if row not in (ROOT / name).read_text(encoding="utf-8"):
            errors.append(f"{name}: metrics row must contain {row} ({result.name})")
    for doc in [*ROOT.glob("README*.md"), ROOT / "SECURITY.md", *ROOT.glob("docs/*.md")]:
        dead = dead_links(doc.read_text(encoding="utf-8"), doc.parent)
        errors += [f"{doc.name}: dead link {t}" for t in dead]
    print("\n".join(errors), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
