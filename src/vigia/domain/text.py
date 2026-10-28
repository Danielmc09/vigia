"""Text helpers shared by commands and signals (domain)."""

import re
import unicodedata
from email.utils import parseaddr


def fold(text: str) -> str:
    """Drop accents one character at a time, so offsets in the result match ``text``.

    Args:
        text: Any text.

    Returns:
        Same length as ``text``, with ``á`` as ``a`` and ``ñ`` as ``n``; case is kept.
    """
    return "".join(unicodedata.normalize("NFD", c)[0] for c in text)


def clean_text(text: str) -> str:
    """Undo the tricks that hide words from patterns and URLs (specs/12 A19).

    NFKC folds fullwidth and compatibility forms to plain letters; format characters
    (zero-width spaces and joiners, bidi overrides such as RTL, BOM, soft hyphen) are
    dropped. Cyrillic homographs are left as they are: the URL signals map them.

    Args:
        text: Text as the member sent it.

    Returns:
        The text with NFKC applied and every ``Cf`` character removed.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf"
    )


def only_digits(raw: str) -> str:
    """Keep only the ASCII digits of ``raw`` (phones, account numbers).

    Args:
        raw: Text as typed (``+57 300-000``, ``cta 12 34``).

    Returns:
        The digits in order, possibly empty.
    """
    return re.sub(r"[^0-9]", "", raw)


def squash(text: str) -> str:
    """Accent-folded, lowercase, ``a-z0-9`` only: how names are compared to hosts and queries.

    Args:
        text: A brand, supplier or query.

    Returns:
        ``"Banco de Bogotá"`` as ``"bancodebogota"``.
    """
    return re.sub(r"[^a-z0-9]", "", fold(text).lower())


def mail_domain(address: str | None) -> str:
    """The domain of an email address or header, without the mailbox (D-064).

    Args:
        address: ``pagos@aceros.co`` or a header such as ``Ana <ana@aceros.co>``.

    Returns:
        ``"aceros.co"``, lowercase; empty when there is no address.
    """
    return parseaddr(address or "")[1].rpartition("@")[2].lower()


# Money as people write it: "$1.200.000", "$3,000", "300 mil", "1,5 millones". A bare number
# ("2023", "256 GB") is not money. ponytail: es/en multipliers only; add per country pack.
_MONEY = re.compile(
    r"(?P<dollar>\$\s?)?(?P<number>\d{1,3}(?:[.,]\d{3})+(?!\d)|\d+(?:[.,]\d{1,2})?)"
    r"(?:\s?(?P<scale>mil(?:lones|lon)?|k)\b)?",
    re.IGNORECASE,
)
_SCALES = {"mil": 1_000, "k": 1_000, "millon": 1_000_000, "millones": 1_000_000}


def amounts(text: str) -> list[float]:
    """Money amounts in ``text``, in order: with ``$``, thousand separators or a multiplier.

    Args:
        text: A message or a chat line.

    Returns:
        ``"$1.200.000 y 300 mil, luego $3,000"`` as ``[1200000, 300000, 3000]``.
    """
    found = []
    for match in _MONEY.finditer(fold(text)):
        number, scale = match["number"], (match["scale"] or "").lower()
        grouped = re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number) is not None
        if not (match["dollar"] or grouped or scale):
            continue
        value = float(re.sub(r"[.,]", "", number) if grouped else number.replace(",", "."))
        found.append(value * _SCALES.get(scale, 1))
    return found
