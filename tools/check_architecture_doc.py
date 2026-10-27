"""Fail if a top-level vigia package from the module map is missing in ARCHITECTURE.md."""

import sys
from collections.abc import Iterable
from pathlib import Path

from repo_map import modules

DOC = Path(__file__).parents[1] / "docs" / "ARCHITECTURE.md"


def missing_packages(doc: str, packages: Iterable[str]) -> list[str]:
    """Packages not named as ``vigia.<package>`` anywhere in the document."""
    return [p for p in packages if f"vigia.{p}" not in doc]


def main() -> int:
    """Check docs/ARCHITECTURE.md against the current source tree."""
    packages = sorted({d.split(".")[1] for d, _ in modules() if d.count(".") >= 1})
    missing = missing_packages(DOC.read_text(encoding="utf-8"), packages)
    if missing:
        print(f"ARCHITECTURE.md does not mention: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
