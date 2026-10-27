"""Known-campaign signal of specs/10 §4: a reported scam seen again is hard evidence (rules)."""

import json
from collections.abc import Iterable, Sequence
from functools import cache
from pathlib import Path
from typing import Any

from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.knowledge import KnownCampaign, campaign_from_seed, hamming, simhash
from vigia.domain.signal import Signal
from vigia.domain.urls import split_url
from vigia.signals.country_pack import country_pack
from vigia.signals.urls import SHORTENERS, registrable_domain

MAX_TEXT_DISTANCE = 6  # specs/10 §4
MAX_IMAGE_DISTANCE = 10  # specs/10 §4


def load_campaigns(path: Path) -> tuple[KnownCampaign, ...]:
    """Read a knowledge-base JSONL file (the seed format), computing the fingerprints.

    Args:
        path: ``conocimiento/semilla.jsonl`` or a test fixture.

    Returns:
        Every campaign in the file; empty when the file does not exist yet.
    """
    if not path.is_file():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(campaign_from_seed(json.loads(line)) for line in lines if line.strip())


def with_campaigns(pack: CountryPack, campaigns: Iterable[KnownCampaign]) -> CountryPack:
    """The pack with the known campaigns of its country attached (D-041).

    Args:
        pack: Country pack.
        campaigns: Campaigns of any country.

    Returns:
        A copy of the pack whose ``campaigns`` are those of ``pack.country``.
    """
    own = tuple(c for c in campaigns if c.country == pack.country)
    return pack.model_copy(update={"campaigns": own})


@cache
def seeded_pack(iso: str, seed: Path) -> CountryPack | None:
    """The country pack with the campaigns of a seed file attached, loaded once.

    The single way the endpoint and ``medir.py`` get a pack, so they never diverge (D-048).

    Args:
        iso: Country code.
        seed: Knowledge-base JSONL file.

    Returns:
        The pack with its campaigns, or ``None`` for an unknown country.
    """
    pack = country_pack(iso)
    return with_campaigns(pack, load_campaigns(seed)) if pack else None


def _hosts(case: Case, pack: CountryPack) -> set[str]:
    """Hosts and registrable domains the case links to, minus official ones and shorteners."""
    hosts = set()
    ignored = pack.official_domains() | SHORTENERS
    for url in case.urls:
        try:
            host = (split_url(url).hostname or "").lower()
        except ValueError:
            continue
        domain = registrable_domain(host)
        if domain not in ignored:
            hosts |= {host, domain}
    return hosts


def text_distance(campaign: KnownCampaign, text_hash: int | None) -> int | None:
    """Hamming distance from a text fingerprint to a campaign, if within the threshold.

    Args:
        campaign: Known campaign.
        text_hash: simhash of the text, or ``None`` if it was too short.

    Returns:
        The distance when it is ``<= MAX_TEXT_DISTANCE``; ``None`` otherwise.
    """
    if text_hash is None or campaign.simhash is None:
        return None
    distance = hamming(text_hash, campaign.simhash)
    return distance if distance <= MAX_TEXT_DISTANCE else None


def _match(
    campaign: KnownCampaign, text_hash: int | None, hosts: set[str], images: Sequence[int]
) -> dict[str, Any] | None:
    """How the case matches one campaign, as evidence; ``None`` if it does not."""
    if (distance := text_distance(campaign, text_hash)) is not None:
        return {"match": "text", "distance": distance}
    if domain := next((d for d in campaign.domains if d in hosts), None):
        return {"match": "domain", "domain": domain}
    if campaign.phash is not None and images:
        distance = min(hamming(campaign.phash, h) for h in images)
        if distance <= MAX_IMAGE_DISTANCE:
            return {"match": "image", "distance": distance}
    return None


def knowledge_signals(
    case: Case, pack: CountryPack, image_hashes: Sequence[int] = ()
) -> list[Signal]:
    """``known_campaign`` (hard) for each known campaign the case repeats.

    A domain of the base that is official in the pack or a shortener never matches: an
    alert that names the real site, or a ``bit.ly`` link, must not condemn every message.

    Args:
        case: Normalized case.
        pack: Country pack with its known campaigns attached.
        image_hashes: pHash of the images and SAM crops of the case (T-12).

    Returns:
        One signal per matched campaign, with ``campaign``, ``source_url`` and how it
        matched in its evidence.
    """
    text_hash = simhash(case.text or "")
    hosts = _hosts(case, pack)
    signals = []
    for campaign in pack.campaigns:
        if evidence := _match(campaign, text_hash, hosts, image_hashes):
            source = {"campaign": campaign.id, "source_url": campaign.source_url}
            signals.append(
                Signal(
                    type="known_campaign",
                    weight="hard",
                    source="knowledge",
                    evidence=source | evidence,
                )
            )
    return signals
