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
