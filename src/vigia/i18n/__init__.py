"""User-facing text catalogs per language; the only place product text lives (specs/13)."""

import json
from functools import cache
from importlib.resources import files

LANGUAGES = ("es", "en")


@cache
def _catalog(lang: str) -> dict[str, str]:
    return json.loads(files(__package__).joinpath(f"{lang}.json").read_text("utf-8"))


def t(lang: str, key: str, **params: str) -> str:
    """Translate a message key into the business language.

    Args:
        lang: Language code of the business (``es`` or ``en``).
        key: Dotted message key, e.g. ``verdict.scam.header``.
        **params: Values for the placeholders in the message.

    Returns:
        The rendered message.

    Raises:
        ValueError: If ``lang`` is not a supported language.
        KeyError: If ``key`` is missing from the catalog.
    """
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language: {lang!r}")
    return _catalog(lang)[key].format(**params)


def has_key(lang: str, key: str) -> bool:
    """Whether the catalog of ``lang`` has ``key``.

    Args:
        lang: Language code of the business.
        key: Dotted message key.

    Returns:
        True if :func:`t` can render it.
    """
    return key in _catalog(lang)
