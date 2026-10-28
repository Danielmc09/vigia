"""Assemble the full :class:`~vigia.domain.verdict.Verdict` of specs/05 (rules 6-7).

:mod:`vigia.verdict.fusion` decides label and confidence; this module adds brand,
official channel, action and the code-built explanation (regla 14, D-043).
"""

from collections.abc import Sequence

from vigia.domain.analyst import Evidence
from vigia.domain.case import Case
from vigia.domain.country import Brand, CountryPack
from vigia.domain.signal import WEIGHT_ORDER, Signal
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


def _has_unofficial_url(case: Case, pack: CountryPack) -> bool:
    """True when any URL of the case (text or QR) is on a domain outside the official ones."""
    official = pack.official_domains()
    for url in case.urls + case.qr_urls:
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
    ordered = sorted(signals, key=lambda s: WEIGHT_ORDER.index(s.weight))
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
    unofficial_url = _has_unofficial_url(case, pack)
    label, confidence = fuse(softened, analyst, evidence.manipulation_detected, unofficial_url)

    all_signals = softened + (list(analyst.signals) if analyst else [])
    brand = _impersonated_brand(all_signals, pack)
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
