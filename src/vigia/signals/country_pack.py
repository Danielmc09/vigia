"""Country packs: brands, domains, channels and patterns per ISO code (rules)."""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

COUNTRIES_DIR = Path(__file__).parent / "countries"
_ISO = re.compile(r"[A-Z]{2}")


@cache
def country_pack(iso: str) -> dict[str, Any] | None:
    """Load the pack of a country.

    Args:
        iso: Two-letter uppercase ISO code.

    Returns:
        The parsed pack, or ``None`` if there is no pack for that code.
    """
    path = COUNTRIES_DIR / f"{iso}.json"
    if not _ISO.fullmatch(iso) or not path.is_file():
        return None
    pack: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return pack
