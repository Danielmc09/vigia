"""Contract of the Jev gate (specs/08 §4): what the judge answers and what the gate decides.

Shared by the gate (``jev``), its HTTP adapter (``model``), the API and the eval runner,
so it lives in the domain.
"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

Triage = Literal["obvious", "investigate", "command"]
"""How a forwarded message should be handled (Jev ``choice`` question of specs/03)."""


class JudgeAnswer(BaseModel, frozen=True):
    """One read of a chat by the judge (Jev, or the mock when Jev is off or down).

    Attributes:
        manipulation: Jev ``noul`` score that the chat tries to change the system, 0 to 1.
        triage: Jev ``choice`` for how to handle the message.
        source: ``jev`` for the development-only judge (D-085), ``spark`` for the
            Meta-model judge used at runtime, ``mock`` for the fallback (specs/12 C10).
        usd: Cost of the call behind this answer (rule 11); 0 for Jev and an unbilled mock.
        tokens: Tokens of that call; 0 for Jev and an unbilled mock.
    """

    manipulation: float = Field(ge=0, le=1)
    triage: Triage
    source: Literal["jev", "spark", "mock"]
    usd: float = 0.0
    tokens: int = 0


MOCK_ANSWER = JudgeAnswer(manipulation=0.0, triage="investigate", source="mock")
"""What the judge answers when Jev is off, keyless or failing: never flags, always investigate."""


def mock_judge(_: str) -> JudgeAnswer:
    """Judge used when Jev is off, keyless or not wanted (tests, adversarial evals).

    Args:
        _: The chat state; ignored.

    Returns:
        Always ``MOCK_ANSWER``.
    """
    return MOCK_ANSWER


Judge = Callable[[str], JudgeAnswer]
"""Port: reads the chat state (history and last message, delimited) and answers."""


class GateResult(BaseModel, frozen=True):
    """What the gate decided for the last message of a chat.

    The gate never blocks (specs/12 C11): the flag only keeps the verdict away from
    ``legit`` and warns the analyst.

    Attributes:
        manipulation_detected: The chat tries to make the system do something else.
        triage: How to handle the message.
        source: ``rules`` when a country-pack pattern fired, else the judge's source.
    """

    manipulation_detected: bool
    triage: Triage
    source: Literal["rules", "jev", "spark", "mock"]
