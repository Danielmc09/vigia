"""Build ``src/vigia/channel/app.js`` from ``web/src`` (D-121); run it with ``make bundle``.

The bundle is committed so nobody needs JavaScript tooling to run VIGÍA. Its first line
carries the hash of the sources, which ``tests/test_panel_react.py`` checks.
"""

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCES = ROOT / "web" / "src"
OUT = ROOT / "src" / "vigia" / "channel" / "app.js"


def source_hash() -> str:
    """SHA-256 of every source file, concatenated in sorted path order."""
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(SOURCES.rglob("*.js*")))
    ).hexdigest()


def main() -> None:
    """Bundle and minify with bun, then prepend the source hash."""
    built = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            "bun", "build", str(SOURCES / "main.jsx"), "--minify", "--target=browser",
            "--define", 'process.env.NODE_ENV="production"',
        ],
        cwd=ROOT / "web", check=True, capture_output=True, text=True,
    ).stdout
    OUT.write_text(f"/* src-sha256: {source_hash()} */\n{built}")


if __name__ == "__main__":
    main()
