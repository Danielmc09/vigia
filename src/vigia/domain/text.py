"""Text helpers shared by commands and signals (domain)."""

import unicodedata


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
