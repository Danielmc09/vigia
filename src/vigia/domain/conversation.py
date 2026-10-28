"""Exported WhatsApp chats: parse, anonymize and read back by role (domain, specs/15).

A chat becomes the case text as one ``<role>: <message>`` line per turn. Roles replace
names: the member's own name never reaches the signals, the analyst or the database.
"""

import re
from collections import Counter
from collections.abc import Callable
from typing import Literal, cast

from vigia.domain.text import amounts, fold

Role = Literal["seller", "buyer", "other"]
Turn = tuple[str, str]
"""``(speaker, message)`` as exported."""

MAX_TURNS = 300
"""specs/12 A28: a long chat keeps its last turns plus every earlier turn with money."""

_DATE = r"\d{1,2}/\d{1,2}/\d{2,4},?\s\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?\s?m\.?)?"
_HEADER = re.compile(rf"^(?:\[{_DATE}\]|{_DATE}\s-)\s?", re.IGNORECASE)
_SPEAKER = re.compile(r"([^:]{1,60}?):\s(.*)", re.DOTALL)
_ROLE_LINE = re.compile(r"(seller|buyer|other): (.*)")
_MIN_TURNS = 2


def parse_chat(text: str) -> list[Turn] | None:
    """Turns of a WhatsApp export, iOS or Android.

    iOS lines look like ``[d/m/y, h:m:s] Name: text``; Android, ``d/m/y h:m - Name: text``.

    Lines without a header continue the previous message; header lines without a speaker
    (encryption notice, "X changed their number") are dropped.

    Args:
        text: The ``.txt`` of "Export chat", already cleaned.

    Returns:
        The turns in order, or ``None`` when ``text`` does not start like an export or has
        fewer than two turns.
    """
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines or not _HEADER.match(lines[0]):
        return None
    chat: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        header = _HEADER.match(line)
        if header is None:
            if current is not None:
                current[1] += "\n" + line
            continue
        speaker = _SPEAKER.fullmatch(line[header.end() :])
        current = [speaker[1].strip(), speaker[2].strip()] if speaker else None
        if current is not None:
            chat.append(current)
    return [(who, said) for who, said in chat] if len(chat) >= _MIN_TURNS else None


def never(_: str) -> bool:
    """``asks``/``keep`` for :func:`render_chat` without a country pack: always ``False``.

    Args:
        _: A chat message or word (ignored).

    Returns:
        Always ``False``.
    """
    return False


def _roles(chat: list[Turn], asks: Callable[[str], bool]) -> dict[str, Role]:
    """Seller = who asks for money most; ties go to who quotes more money, then not first.

    The victim may quote more amounts than the seller ("ya le transferí los $300.000"), so
    amounts alone only break ties; ``asks`` (from the country pack) reads who demands them.

    ponytail: heuristic; a buyer who quotes the seller's demand ("me pide anticipo por
    Nequi") counts as asking. Upgrade: ask the member once, as with ``origen`` (specs/15).
    """
    demands = Counter(who for who, said in chat if asks(said))
    money = Counter(who for who, said in chat if amounts(said))
    speakers = list(dict.fromkeys(who for who, _ in chat))
    seller = max(speakers, key=lambda who: (demands[who], money[who], speakers.index(who) > 0))
    buyer = next(who for who in speakers if who != seller)
    return {
        who: "seller" if who == seller else "buyer" if who == buyer else "other" for who in speakers
    }


def _mask(roles: dict[str, Role], keep: Callable[[str], bool]) -> Callable[[str], str]:
    """Replace each speaker's full name and name words with the speaker's role (regla 19).

    Compared without accents or case ("Andres" masks "Andrés"). A name word that ``keep``
    says is a common word of the trade ("criadero") is masked only inside the full name.
    """
    names: dict[str, Role] = {}
    for who, role in roles.items():
        words = re.findall(r"[^\W\d_]{3,}", who)  # letters only: "+57 312 150" keeps "150 mil"
        names.update({fold(w).casefold(): role for w in words if not keep(w)})
        names[fold(" ".join(who.split())).casefold()] = role
    if not names:
        return lambda said: said
    alternatives = "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
    pattern = re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)

    def mask(said: str) -> str:
        spans = list(pattern.finditer(fold(said)))  # fold keeps offsets
        for m in reversed(spans):
            said = said[: m.start()] + names[m[0].casefold()] + said[m.end() :]
        return said

    return mask


def render_chat(chat: list[Turn], asks: Callable[[str], bool], keep: Callable[[str], bool]) -> str:
    """The chat as case text: ``<role>: <message>`` lines, no speaker's name in clear.

    Args:
        chat: Turns from :func:`parse_chat` (at least two speakers).
        asks: Whether a message demands payment; picks the seller (see :func:`_roles`).
        keep: Whether a word of a speaker's name is a common word that stays in clear.

    Returns:
        One line per turn (multi-line messages are joined with spaces). Above
        :data:`MAX_TURNS`, earlier turns survive only if they mention money.
    """
    roles = _roles(chat, asks)
    mask = _mask(roles, keep)
    cut = len(chat) - MAX_TURNS
    kept = [turn for i, turn in enumerate(chat) if i >= cut or amounts(turn[1])]
    return "\n".join(f"{roles[who]}: {mask(' '.join(said.split()))}" for who, said in kept)


def turns(text: str) -> list[tuple[Role | None, str]]:
    """Read case text back as turns; a plain message is one turn with no role.

    Args:
        text: Case text, rendered by :func:`render_chat` or as forwarded.

    Returns:
        ``(role, message)`` per ``<role>:`` line; any other text is a single ``None`` turn.
    """
    lines = text.splitlines()
    parsed = [_ROLE_LINE.fullmatch(line) for line in lines]
    if lines and all(parsed):
        return [(cast(Role, m[1]), m[2]) for m in parsed if m]
    return [(None, text)]
