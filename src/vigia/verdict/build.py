"""Assemble the full :class:`~vigia.domain.verdict.Verdict` of specs/05 (rules 6-7).

:mod:`vigia.verdict.fusion` decides label and confidence; this module adds brand,
official channel, action and the code-built explanation (regla 14, D-043).
"""

import re
from collections.abc import Sequence

from vigia.domain.analyst import Evidence
from vigia.domain.case import Case
from vigia.domain.country import Brand, CountryPack
from vigia.domain.signal import Signal, by_weight
from vigia.domain.text import fold
from vigia.domain.urls import split_url
from vigia.domain.verdict import Action, Label, Verdict
from vigia.i18n import has_key, t
from vigia.signals.urls import registrable_domain
from vigia.verdict.fusion import LEGIT, SCAM, fuse, soften_citations

_SALE_ACTIONS: dict[str, Action] = {"extortion": "block_and_report", "already_paid": "stop_paying"}
"""specs/15 reply variants, first match wins: threats, then a victim who already paid."""

MAX_EXPLANATION_SIGNALS = 3  # specs/05 template: "Señales: {hasta 3}"

# specs/05 rule 7: a hard signal about money asks the member not to pay; any other hard
# signal (a link) asks not to open it.
_PAY_HARD_TYPES = frozenset(
    {
        "personal_payment_release",
        "account_differs_from_known",
        "supplier_from_unknown_origin",
    }
)


def _has_unofficial_url(case: Case, signals: list[Signal], pack: CountryPack) -> bool:
    """True when any URL of the case (text or QR) is on a domain outside the official ones.

    A text link unrolled to an official domain counts as official here, and only here: this
    flag guards the legit branch, so a cloaked shortener can at most keep a case doubtful
    (D-098). QR links are never unrolled. Its raw twin, which escalates credential requests, lives
    in signals/detector.py and never looks at unrolled links.
    """
    official = pack.official_domains()
    landed = {
        s.evidence.get("via") for s in signals if s.type == "official_domain" and s.source == "urls"
    }
    for url in [u for u in case.urls if u not in landed] + case.qr_urls:
        try:
            domain = registrable_domain(split_url(url).netloc)
        except ValueError:
            continue
        if domain not in official:
            return True
    return False


def _impersonated_brand(signals: Sequence[Signal], pack: CountryPack) -> Brand | None:
    """The pack brand a hard signal says is being imitated, if any.

    Regla 14: evidence may come from the analyst, so a ``brand`` is only trusted when it
    is exactly the name of a brand of the pack; anything else is dropped, never echoed.
    """
    by_name = {b.name: b for b in pack.brands}
    for s in signals:
        if s.weight == "hard" and (brand := by_name.get(str(s.evidence.get("brand")))):
            return brand
    for s in signals:
        if s.weight == "hard" and s.type == "lookalike_domain":
            official = s.evidence.get("official")
            if brand := next((b for b in pack.brands if official in b.domains), None):
                return brand
    return None


def _brand_in_body(text: str, pack: CountryPack) -> Brand | None:
    """The pack brand written as a whole word in the body (incident c-0211).

    Case-insensitive ("CoordInadora", an ASR "dian"), except that an all-lowercase
    match of an ambiguous brand is skipped so plain words like "dale" or "claro" do not
    name it (D-104).
    """
    # ponytail: an ambiguous brand capitalized at the start ("Dale clic") is still named.
    folded = fold(text).lower()
    for brand in pack.brands:
        name = re.escape(fold(brand.name).lower())
        for found in re.finditer(rf"(?<![a-z0-9]){name}(?![a-z0-9])", folded):
            if not (brand.ambiguous and text[found.start() : found.end()].islower()):
                return brand
    return None


def _action(label: Label, signals: Sequence[Signal]) -> Action:
    """specs/05 rule 7: what the member should do."""
    if label == LEGIT:
        return "ignore"
    if label != SCAM:
        return "verify_official_channel"
    hard = [s for s in signals if s.weight == "hard"]
    sale = {s.type for s in signals if s.source == "sequence"}
    # specs/15: the marketplace reply only when a sale signal (not just "already paid")
    # carries the verdict and no other source's hard signal (link, brand, KB) does.
    if any(s.source == "sequence" and s.weight != "light" for s in signals) and all(
        s.source == "sequence" for s in hard
    ):
        return next((a for k, a in _SALE_ACTIONS.items() if k in sale), "dont_pay_sale")
    if any(s.type in _PAY_HARD_TYPES for s in hard):
        return "dont_pay"
    if hard:
        return "dont_open_link"
    return "verify_official_channel"


def _explanation(lang: str, label: Label, signals: Sequence[Signal], brand: str | None) -> str:
    """specs/05 rule 6: the user-facing text, always code-built from i18n (regla 14).

    The analyst's own free text never reaches this (see :class:`AnalystResult`), and
    neither do its signals: callers pass only deterministic evidence, so the model never
    picks which facts the code asserts (D-065).
    """
    header = (
        t(lang, f"verdict.{SCAM}.brand", brand=brand)
        if label == SCAM and brand
        else t(lang, f"verdict.{label}.header")
    )
    ordered = by_weight(signals)
    # a type with no catalog entry (e.g. made up by the analyst) is left out, never echoed
    known = (s.type for s in ordered if has_key(lang, f"signal.{s.type}"))
    names = list(dict.fromkeys(known))[:MAX_EXPLANATION_SIGNALS]
    if not names:
        return header
    rendered = ", ".join(t(lang, f"signal.{name}") for name in names)
    return t(lang, "verdict.explanation.signals", header=header, signals=rendered)


def build_verdict(case: Case, evidence: Evidence, pack: CountryPack) -> Verdict:
    """Fold the signals and the analyst into the single :class:`Verdict` for a case.

    Args:
        case: The case being decided.
        evidence: Its signals, the analyst's read if one ran and Jev's manipulation flag.
        pack: Country pack of the business.

    Returns:
        The verdict, with every field of specs/05 filled.
    """
    analyst = evidence.analyst
    softened = soften_citations(evidence.signals, case, pack)
    unofficial_url = _has_unofficial_url(case, evidence.signals, pack)
    label, confidence = fuse(softened, analyst, evidence.manipulation_detected, unofficial_url)

    all_signals = softened + (list(analyst.signals) if analyst else [])
    brand = _impersonated_brand(all_signals, pack)
    if brand is None and label == SCAM:
        brand = _brand_in_body(case.text or "", pack)
    return Verdict(
        case_id=str(case.id),
        business_id=str(case.business_id),
        label=label,
        confidence=confidence,
        signals=all_signals,
        impersonated_brand=brand.name if brand else None,
        user_explanation=_explanation(
            pack.language, label, softened, brand.name if brand else None
        ),
        action=_action(label, all_signals),
        official_channel=brand.official_channel if brand else None,
        degraded=analyst is None,
        manipulation_detected=evidence.manipulation_detected,
        tools_used=list(analyst.tools_used) if analyst else [],
        model=dict(analyst.model) if analyst else {},
        total_latency_ms=analyst.latency_ms if analyst else 0,
    )
