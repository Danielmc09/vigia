"""Known scam campaigns and their fingerprints: simhash of text, pHash of images (domain).

Only fingerprints, domains and brand are kept, never a message (D-017); specs/10 §4.
"""

import math
import re
from collections.abc import Sequence
from datetime import date
from hashlib import blake2b
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from vigia.domain.text import clean_text, fold
from vigia.domain.urls import extract_urls

CampaignKind = Literal["text", "domain", "image"]
SourceType = Literal["alerta_oficial", "banco", "prensa", "policia", "foro", "usuario"]

BITS = 64
MIN_WORDS = 8  # D-040: fewer words collide by chance ("hola, cómo estás")
PHASH_SIDE = 32  # pHash input: a 32x32 grayscale grid
PHASH_LOW = 8  # pHash keeps the 8x8 lowest frequencies
_WORD = re.compile(r"[a-z]{2,}")


class KnownCampaign(BaseModel):
    """A scam already reported somewhere public (or confirmed by a business owner).

    Attributes:
        id: Stable id, e.g. ``kb-co-001``.
        country: ISO code of the country where it circulates.
        kind: What identifies it: its text, its domains or an image.
        brand: Impersonated brand, if any.
        simhash: 64-bit simhash of its text; ``None`` when matched by domain or image only.
        phash: 64-bit pHash of its image, if any.
        domains: Hosts or registrable domains it used, matched exactly.
        source_url: Public alert that documents it.
        source_type: Who published that alert.
        seen_date: When it was seen.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    country: str
    kind: CampaignKind
    brand: str | None = None
    simhash: int | None = None
    phash: int | None = None
    domains: tuple[str, ...] = ()
    source_url: str
    source_type: SourceType
    seen_date: date


def words(text: str) -> list[str]:
    """Words of a message as a campaign fingerprint sees them.

    URLs and numbers are what changes between copies of one campaign, so they go;
    case, accents, punctuation and invisible characters go too.

    Args:
        text: Message text.

    Returns:
        Lowercase ASCII words of two letters or more, in order.
    """
    kept = (token for token in clean_text(text).split() if not extract_urls(token))
    return _WORD.findall(fold(" ".join(kept)).lower())


def simhash(text: str) -> int | None:
    """64-bit simhash of the words of a text (Charikar), one feature per word.

    Args:
        text: Message text.

    Returns:
        The fingerprint, or ``None`` with fewer than ``MIN_WORDS`` words.
    """
    found = words(text)
    if len(found) < MIN_WORDS:
        return None
    votes = [0] * BITS
    for word in found:
        bits = int.from_bytes(blake2b(word.encode(), digest_size=BITS // 8).digest())
        for i in range(BITS):
            votes[i] += 1 if bits >> i & 1 else -1
    return sum(1 << i for i, vote in enumerate(votes) if vote > 0)


def phash(gray: Sequence[Sequence[float]]) -> int:
    """64-bit perceptual hash of a 32x32 grayscale grid (DCT, 8x8 low frequencies vs median).

    Decoding and resizing an image into the grid needs an image library (T-12, D-033).

    Args:
        gray: 32 rows of 32 luminance values.

    Returns:
        The fingerprint; a brightness shift leaves it unchanged.

    Raises:
        ValueError: If the grid is not 32x32.
    """
    if len(gray) != PHASH_SIDE or any(len(row) != PHASH_SIDE for row in gray):
        raise ValueError(f"pHash needs a {PHASH_SIDE}x{PHASH_SIDE} grid")
    n = PHASH_SIDE
    basis = [
        [math.cos(math.pi * (2 * x + 1) * u / (2 * n)) for x in range(n)] for u in range(PHASH_LOW)
    ]
    rows = [[sum(r[x] * basis[u][x] for x in range(n)) for u in range(PHASH_LOW)] for r in gray]
    coeffs = [
        sum(rows[y][u] * basis[v][y] for y in range(n))
        for v in range(PHASH_LOW)
        for u in range(PHASH_LOW)
    ]
    middle = median(coeffs[1:])  # the DC term only measures brightness
    return sum(1 << i for i, c in enumerate(coeffs[1:], 1) if c > middle)


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two fingerprints."""
    return (a ^ b).bit_count()


def campaign_from_seed(record: dict[str, Any]) -> KnownCampaign:
    """A campaign from one line of ``conocimiento/semilla.jsonl``, fingerprints computed.

    Args:
        record: ``id, country, kind, brand, text, domains, source_url, source_type,
            seen_date``. The text of a ``domain`` entry describes it and is not hashed.

    Returns:
        The campaign, with lowercase domains and without the text.
    """
    return KnownCampaign(
        id=record["id"],
        country=record["country"],
        kind=record["kind"],
        brand=record.get("brand"),
        simhash=simhash(record["text"]) if record["kind"] == "text" else None,
        domains=tuple(d.lower() for d in record.get("domains") or ()),
        source_url=record["source_url"],
        source_type=record["source_type"],
        seen_date=record["seen_date"],
    )
