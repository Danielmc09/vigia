"""The Jev gate of specs/08 §4: does this chat try to make the system do something else?

Deterministic floor first (regla 5): a ``manipulacion`` pattern of the country pack in the
message or in the recent history flags it, whatever Jev says. Otherwise the judge decides.
"""

import html
from collections.abc import Sequence

from vigia.domain.country import CountryPack
from vigia.domain.gate import GateResult, Judge
from vigia.domain.text import clean_text
from vigia.signals.patterns import find_pattern

MAX_HISTORY = 10  # specs/08 §4: the message and the last 10 of the same chat
MANIPULATION_THRESHOLD = 0.7  # Jev noul score that flags manipulation (D-051)


def _state(text: str, history: Sequence[str]) -> str:
    """The chat as Jev reads it: every turn delimited, the last one marked as such.

    User text is HTML-escaped so a ``</last_turn>`` inside a message cannot close the
    delimiter and pose as the system (incident ``adv-delimitador-jev``).
    """
    turns = [f"<turn>{html.escape(h, quote=False)}</turn>" for h in history]
    return "\n".join([*turns, f"<last_turn>{html.escape(text, quote=False)}</last_turn>"])


def _flat(text: str) -> str:
    """Clean text with every run of blanks as one space.

    So ``ignora   tus`` or a line break inside an order still matches (incidents
    adv-espacios-inyeccion, adv-salto-linea-inyeccion).
    """
    return " ".join(clean_text(text).split())


def screen(text: str, history: Sequence[str], pack: CountryPack, judge: Judge) -> GateResult:
    """Screen the last message of a chat for manipulation and triage it.

    Args:
        text: The message just received.
        history: Earlier texts of the same chat, oldest first; only the last
            ``MAX_HISTORY`` count.
        pack: Country pack with the ``manipulacion`` patterns.
        judge: Jev, or the mock (never raises: falls back to ``MOCK_ANSWER``).

    Returns:
        The gate decision; ``source == "rules"`` when a pattern fired (Jev is not asked).
    """
    recent = list(history)[-MAX_HISTORY:]
    if any(find_pattern(_flat(t), pack, "manipulacion") for t in [*recent, text]):
        return GateResult(manipulation_detected=True, triage="investigate", source="rules")
    answer = judge(_state(text, recent))
    return GateResult(
        manipulation_detected=answer.manipulation >= MANIPULATION_THRESHOLD,
        triage=answer.triage,
        source=answer.source,
    )
