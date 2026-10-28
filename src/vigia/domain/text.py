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
