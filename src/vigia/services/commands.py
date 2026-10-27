"""Enrollment commands recognized in any supported language, with no model (specs/01)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from vigia.domain.text import fold
from vigia.i18n import LANGUAGES, t

# What must follow each keyword for the message to be a command and not a case to analyze.
# The only grammar of each command: handlers read the named groups, never re-parse.
_EMAIL = r"\S+@\S+\.\S+"
_GRAMMAR = {
    # Without name and country it is still ``enroll``: the handler explains how to enroll.
    "enroll": re.compile(r"(?P<name>.+?)\s+(?P<country>[A-Za-z]{2})|.*", re.S),
    "team": re.compile(
        rf"(?P<phone>\+\d[\d\s-]{{6,18}}\d)\s+(?P<name>[^\d\s+@][^@]*?)(?:\s+(?P<email>{_EMAIL}))?"
    ),
    "my_email": re.compile(rf"(?P<email>{_EMAIL})"),
    "code": re.compile(r"(?P<code>\d{6})"),
    "supplier": re.compile(r"(?P<name>[^=]+?)\s+(?P<pairs>\S+=\S+(?:\s+\S+=\S+)*)"),
    "history": re.compile(r""),
    "help": re.compile(r""),
}


@dataclass(frozen=True)
class Command:
    """A recognized command.

    Attributes:
        name: Command id (``enroll``, ``team``, ``my_email``, ``code``, ``supplier``,
            ``history``, ``help``).
        args: Text after the keyword, stripped.
        lang: Language whose keyword matched; used to answer someone not yet enrolled.
        fields: Named groups of the command grammar (``""`` for an absent optional part).
    """

    name: str
    args: str
    lang: str
    fields: Mapping[str, str] = field(default_factory=dict)


def parse_command(text: str | None) -> Command | None:
    """Recognize a command; anything else is a case to analyze.

    Keywords come from the i18n catalogs (``command.*``) and match case- and
    accent-insensitively. A keyword followed by text that does not fit the command
    (``historial de pagos``) is not a command.

    Args:
        text: Message body.

    Returns:
        The command, or ``None``.
    """
    if not text or not text.strip():
        return None
    for lang in LANGUAGES:
        for name, grammar in _GRAMMAR.items():
            keyword = t(lang, f"command.{name}")
            words = keyword.count(" ") + 1
            parts = text.strip().split(maxsplit=words)
            if fold(" ".join(parts[:words])).lower() != keyword:
                continue
            args = parts[words] if len(parts) > words else ""
            if match := grammar.fullmatch(args):
                return Command(name, args, lang, match.groupdict(default=""))
    return None
