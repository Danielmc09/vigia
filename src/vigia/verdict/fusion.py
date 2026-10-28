"""Fusion of specs/05: signals, and the analyst when present, decide the label.

Rules 1-5 and the citation softening of D-044 live here; :mod:`vigia.verdict.build`
turns the label into the full :class:`~vigia.domain.verdict.Verdict` (rules 6-7).
"""

import re
from collections.abc import Sequence
from typing import cast, get_args

from vigia.domain.analyst import AnalystResult
from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.signal import Signal
from vigia.domain.text import fold
from vigia.domain.verdict import Label
from vigia.signals.patterns import find_pattern

SCAM, LEGIT, DOUBTFUL = cast(tuple[Label, Label, Label], get_args(Label))
HARD_CONFIDENCE = 0.9  # specs/05 rule 1
MEDIUMS_FOR_SCAM = 2  # specs/05 rule 2; distinct signal types (D-034)
MEDIUMS_CONFIDENCE = 0.8
DOUBTFUL_CONFIDENCE = 0.5
LEGIT_CONFIDENCE_FLOOR = 0.7  # specs/05 rule 4: the analyst needs this much to call it legit


def fuse(
    signals: Sequence[Signal],
    analyst: AnalystResult | None = None,
    manipulation_detected: bool = False,
    has_unofficial_url: bool = False,
) -> tuple[Label, float]:
    """Label and confidence from the deterministic signals and, when present, the analyst.

    Args:
        signals: Signals of the case (hard signals should already be softened by
            :func:`soften_citations` if the caller wants that rule applied).
        analyst: The analyst's own read of the case, if one ran (T-09); without it the
            label can never be ``legit`` (rule 4) nor more confident than a signal alone
            gives (rule 1).
        manipulation_detected: True once Jev (T-08) flagged the message; forces at least
            ``doubtful``, never ``legit`` (rule 5).
        has_unofficial_url: True when the case carries a link outside the official
            domains; gates the analyst-alone ``legit`` branch of rule 4.

    Returns:
        The label and its confidence.
    """
    if _has_hard(signals):
        label, confidence = SCAM, HARD_CONFIDENCE
        if analyst and analyst.label == SCAM:
            confidence = max(confidence, analyst.confidence)
    else:
        mediums = _medium_types(signals)
        if len(mediums) >= MEDIUMS_FOR_SCAM:
            label, confidence = SCAM, MEDIUMS_CONFIDENCE
        elif len(mediums) == 1:
            label, confidence = _rule3(analyst)
        else:
            label, confidence = _rule4(analyst, has_unofficial_url)
    if manipulation_detected and label == LEGIT:
        label, confidence = DOUBTFUL, DOUBTFUL_CONFIDENCE
    return label, confidence


def _has_hard(signals: Sequence[Signal]) -> bool:
    return any(s.weight == "hard" for s in signals)


def _medium_types(signals: Sequence[Signal]) -> set[str]:
    return {s.type for s in signals if s.weight == "medium"}


def decided_by_rules(signals: Sequence[Signal]) -> bool:
    """True when rules 1-2 already make it a scam whatever the analyst says.

    The analyst could only raise the confidence of a hard hit, so the caller may skip it
    (D-061).

    Args:
        signals: Signals of the case, already softened by :func:`soften_citations`.

    Returns:
        True with a hard signal or ``MEDIUMS_FOR_SCAM`` distinct medium types.
    """
    return _has_hard(signals) or len(_medium_types(signals)) >= MEDIUMS_FOR_SCAM


def _rule3(analyst: AnalystResult | None) -> tuple[Label, float]:
    """specs/05 rule 3: exactly one medium signal, the analyst breaks the tie."""
    if analyst and analyst.label == SCAM:
        return SCAM, analyst.confidence
    return DOUBTFUL, DOUBTFUL_CONFIDENCE


def _rule4(analyst: AnalystResult | None, has_unofficial_url: bool) -> tuple[Label, float]:
    """specs/05 rule 4: no medium or hard signal, the analyst alone decides."""
    if analyst is None:
        return DOUBTFUL, DOUBTFUL_CONFIDENCE
    if analyst.label == LEGIT:
        if analyst.confidence >= LEGIT_CONFIDENCE_FLOOR and not has_unofficial_url:
            return LEGIT, analyst.confidence
        return DOUBTFUL, DOUBTFUL_CONFIDENCE
    return analyst.label, analyst.confidence


_CLAUSE_END = re.compile(r"[.!?;:,\n]+(?=\s|$)")
# Words allowed between a denial verb and the domain: "no entren a diangov.com",
# "do not click the link diangov.com". ponytail: fixed list, extend per incident.
_CONNECTORS = re.compile(
    r"(?:\s+(?:a|al|en|el|la|ese|esa|este|enlace|link|sitio|pagina|to|at|on|the"
    r"|that|this|site))*\s*",
    re.IGNORECASE,
)


def _denies(text: str, pack: CountryPack, *, at_start: bool) -> bool:
    """Whether a ``negacion`` pattern opens (or closes) ``text``, with only connectors around."""
    match = find_pattern(text, pack, "negacion")
    if match is None:
        return False
    if at_start:
        return not text[: match.start()].strip()
    return _CONNECTORS.fullmatch(fold(text[match.end() :])) is not None


def _is_citation_as_warning(signal: Signal, case: Case, pack: CountryPack) -> bool:
    """Whether the case denies the quoted known-scam domain instead of inviting a click.

    D-044 (c-0076, c-0077..c-0082): only a denial whose object is the domain softens the
    hit: a denial verb right before it ("no entren a diangov.com") or a denial predicate
    right after it ("diangov.com es falso"), within the same clause. An alert word alone
    ("ALERTA", "Warning") or a denial of something else in the sentence ("no entre en
    pánico ... diangov.com") never does; neither does a text-hash or image match.
    """
    domain = str(signal.evidence.get("domain") or "").lower()
    if signal.type != "known_campaign" or not domain or not case.text:
        return False
    text = case.text
    start = text.lower().find(domain)
    while start != -1:
        before = _CLAUSE_END.split(text[:start])[-1]
        after = _CLAUSE_END.split(text[start + len(domain) :].lstrip(" ,"))[0]
        if _denies(before, pack, at_start=False) or _denies(after, pack, at_start=True):
            return True
        start = text.lower().find(domain, start + 1)
    return False


def soften_citations(signals: Sequence[Signal], case: Case, pack: CountryPack) -> list[Signal]:
    """Downgrade a ``known_campaign`` hard signal that merely quotes the domain to warn.

    Args:
        signals: Signals of the case, as produced by the detectors.
        case: The case they belong to (its text is checked for warning language).
        pack: Country pack, for its ``negacion`` patterns.

    Returns:
        The same signals, with any citation-as-warning ``known_campaign`` hit downgraded
        to ``light`` instead of ``hard``; everything else is unchanged.
    """
    return [
        s.model_copy(update={"weight": "light"}) if _is_citation_as_warning(s, case, pack) else s
        for s in signals
    ]
