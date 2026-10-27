"""Text signals of specs/04: country-pack regexes over the case text (rules)."""

import re
from functools import cache

from vigia.domain.country import CountryPack
from vigia.domain.signal import Signal, Weight
from vigia.domain.text import fold

MEDIUM_CATEGORIES = {"urgencia": "urgency", "premio": "prize", "familiar": "family"}
"""Pack categories that are a medium signal on their own, and the signal they produce."""

LONG_TEXT_CHARS = 4000
"""Above this the analyst gets a truncated text (specs/12 A2); the length is a light signal."""


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    """Whole-word, case- and accent-insensitive regex for one pack pattern."""
    return re.compile(rf"\b(?:{fold(pattern)})\b", re.IGNORECASE)


def find_pattern(text: str, pack: CountryPack, category: str) -> re.Match[str] | None:
    """First match of any pattern of a pack category, over the accent-folded text.

    Args:
        text: Text to search.
        pack: Country pack with the regex lists.
        category: Pack category (``urgencia``, ``cuenta_bancaria``...); missing means none.

    Returns:
        The match; its offsets are valid in ``text`` (see :func:`vigia.domain.text.fold`).
    """
    folded = fold(text)
    for pattern in pack.patterns.get(category, ()):
        if match := _compile(pattern).search(folded):
            return match
    return None


def pattern_signals(text: str, pack: CountryPack, unofficial_link: bool) -> list[Signal]:
    """Signals from the text patterns of the country pack.

    Credentials requested next to a non-official link, or a personal-wallet payment to
    "release" a parcel, prize or debt, are hard; urgency, prize and family are medium;
    authority is medium only next to a non-official link; a text longer than
    ``LONG_TEXT_CHARS`` is a light signal.

    Args:
        text: Case text (body, caption or transcription).
        pack: Country pack of the business.
        unofficial_link: True when the case carries a link outside the official domains.

    Returns:
        The signals that fired, with the matched text as evidence.
    """
    signals: list[Signal] = []

    def add(kind: str, weight: Weight, category: str) -> None:
        if match := find_pattern(text, pack, category):
            evidence = {"match": text[match.start() : match.end()]}
            signals.append(Signal(type=kind, weight=weight, source="patterns", evidence=evidence))

    add("credentials_request", "hard" if unofficial_link else "light", "pide_credenciales")
    if find_pattern(text, pack, "liberar"):
        add("personal_payment_release", "hard", "pago_personal")
    else:
        add("personal_payment", "light", "pago_personal")
    for category, kind in MEDIUM_CATEGORIES.items():
        add(kind, "medium", category)
    if unofficial_link:
        add("authority_with_link", "medium", "autoridad")
    if len(text) > LONG_TEXT_CHARS:
        evidence = {"chars": len(text)}
        signals.append(
            Signal(type="text_too_long", weight="light", source="patterns", evidence=evidence)
        )
    return signals
