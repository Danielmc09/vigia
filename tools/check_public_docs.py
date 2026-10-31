"""Fail if the README metrics or routing figures are stale, or a doc links to an ignored path."""

import json
import re
import statistics
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


def routing_fragments(cases: list[dict[str, Any]], mark: str) -> list[str]:
    """README phrases on which cases skipped the analyst, in the language of ``mark``.

    ``degradado`` means "no analyst" (D-061): with no tools the rules were conclusive; with
    tools a model only read the attachment before the rules decided.
    """
    rules = [c for c in cases if c["degradado"] and not c["herramientas_usadas"]]
    readers = sum(1 for c in cases if c["degradado"] and c["herramientas_usadas"])
    analyst = [c for c in cases if not c["degradado"]]

    def cost(group: list[dict[str, Any]]) -> tuple[str, str]:
        usd = f"${statistics.median(c['usd'] for c in group):.4f}"
        secs = f"{statistics.median(c['ms'] for c in group) / 1000:.1f}"
        return usd.replace(".", mark), secs.replace(".", mark)

    (ru, rs), (au, as_) = cost(rules), cost(analyst)
    if mark == ",":
        return [
            f"En {len(rules)} de los {len(cases)} casos",
            f"mediana de {ru} y {rs} s por caso, contra {au} y {as_} s con el analista",
            f"En otros {readers},",
            f"El analista corrió en los {len(analyst)} restantes.",
        ]
    return [
        f"In {len(rules)} of the {len(cases)} cases",
        f"median of {ru} and {rs} s per case, against {au} and {as_} s with the analyst",
        f"In {readers} more,",
        f"The analyst ran in the other {len(analyst)}.",
    ]


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
        prose = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        for phrase in routing_fragments(run["casos"], mark):
            if phrase not in prose:
                errors.append(f"{name}: must say '{phrase}' ({result.name})")
    for doc in [*ROOT.glob("README*.md"), ROOT / "SECURITY.md", *ROOT.glob("docs/*.md")]:
        dead = dead_links(doc.read_text(encoding="utf-8"), doc.parent)
        errors += [f"{doc.name}: dead link {t}" for t in dead]
    print("\n".join(errors), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
