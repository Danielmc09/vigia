"""Enrollment commands recognized in any supported language, with no model (specs/01)."""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from vigia.domain.text import clean_text, fold
from vigia.domain.urls import extract_urls
from vigia.i18n import LANGUAGES, t

# What must follow each keyword for the message to be a command and not a case to analyze.
# The only grammar of each command: handlers read the named groups, never re-parse.
_EMAIL = r"\S+@\S+\.\S+"
# With or without "+", spaces, dashes, dots or "(321)"; the handler checks the digit count.
_PHONE = r"\+?\(?\d[\d\s().-]{6,18}\d"
_GRAMMAR = {
    # Without name and country it is still ``enroll``: the handler explains how to enroll.
    "enroll": re.compile(r"(?P<name>.+?)\s+(?P<country>[A-Za-z]{2})|.*", re.S),
    "team": re.compile(
        rf"(?P<phone>{_PHONE})\s+(?P<name>[^\d\s+@][^@]*?)(?:\s+(?P<email>{_EMAIL}))?"
    ),
    "my_email": re.compile(rf"(?P<email>{_EMAIL})"),
    "code": re.compile(r"(?P<code>\d{6})"),
    "supplier": re.compile(r"(?P<name>[^=]+?)\s+(?P<pairs>\S+=\S+(?:\s+\S+=\S+)*)"),
    "history": re.compile(r""),
    "help": re.compile(r""),
    # confirmar/cancelar/transferir (specs/05, specs/12 B7, D-047). The id is the 8 hex
    # digits the policy quotes; a short "confirmar compra" gets the usage reply (D-092).
    "confirm": re.compile(r"(?P<id>[0-9A-Fa-f]{8})"),
    "cancel": re.compile(r"(?P<id>[0-9A-Fa-f]{8})"),
    "transfer": re.compile(rf"(?P<phone>{_PHONE})"),
    # ``estafa confirmada <case number>``: the short number the reply quotes (D-091).
    "scam_confirmed": re.compile(r"(?P<ref>\d{1,9})"),
}
# Keywords that take arguments: a short message opening with one is a command typed wrong.
_WITH_ARGS = [name for name, grammar in _GRAMMAR.items() if grammar.pattern and name != "enroll"]
USAGE_MAX_WORDS = 6
"""A message this short that opens with a keyword is a command attempt, not a forward (D-092)."""


@dataclass(frozen=True)
class Command:
    """A recognized command.

    Attributes:
        name: Command id (``enroll``, ``team``, ``my_email``, ``code``, ``supplier``,
            ``history``, ``help``, ``transfer``, ``confirm``, ``cancel``, ``scam_confirmed``).
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
    for name, lang, args in _keywords(text):
        if match := _GRAMMAR[name].fullmatch(args):
            return Command(name, args, lang, match.groupdict(default=""))
    return None


def misused_command(text: str | None) -> str | None:
    """The command a short message opening with its keyword tried and got wrong.

    Call it only after :func:`parse_command` returned ``None``. A long message or one with
    a link is a forward that happens to open with the word (``Equipo de seguridad...``),
    and still goes to analysis.

    Args:
        text: Message body.

    Returns:
        The command name, whose ``usage.<name>`` reply shows the right format, or ``None``.
    """
    if not text or len(text.split()) > USAGE_MAX_WORDS or extract_urls(text):
        return None
    return next((name for name, _, _ in _keywords(text) if name in _WITH_ARGS), None)


def _keywords(text: str | None) -> Iterator[tuple[str, str, str]]:
    """Every ``(command, language, args)`` whose keyword opens ``text``.

    The text is cleaned first: WhatsApp wraps a copied number in bidi marks and may use
    non-breaking spaces, which would otherwise keep the grammar from matching.
    """
    text = clean_text(text or "").strip()
    for lang in LANGUAGES:
        for name in _GRAMMAR:
            keyword = t(lang, f"command.{name}")
            words = keyword.count(" ") + 1
            parts = text.split(maxsplit=words)
            if parts and fold(" ".join(parts[:words])).lower() == keyword:
                yield name, lang, parts[words] if len(parts) > words else ""
