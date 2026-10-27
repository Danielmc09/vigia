"""The analyst's own read of a case, before fusion folds it with deterministic signals.

T-09 builds the real tool-using loop that produces this; T-07 only needs the contract so
fusion and the endpoint can be tested against a mock (domain).
"""

from typing import Any

from pydantic import BaseModel, Field

from vigia.domain.signal import Signal
from vigia.domain.verdict import Label


class AnalystResult(BaseModel, frozen=True):
    """What the analyst concluded about one case.

    ``explanation`` is the analyst's own reasoning, kept for logs and
    ``herramientas_usadas`` only: AGENTS regla 14 forbids the model's free text from
    reaching the user, so the verdict's ``user_explanation`` is always built by code
    from i18n templates instead (D-043).

    Attributes:
        label: The analyst's verdict guess.
        confidence: How sure it is, 0 to 1.
        explanation: Its reasoning, for logs only.
        signals: Extra evidence it found, source ``analyst``.
        tools_used: One entry per tool call (``name``, ``ms``, ``usd``).
        model: Model identification (name, version...).
        latency_ms: How long the analyst took.
    """

    label: Label
    confidence: float = Field(ge=0, le=1)
    explanation: str = ""
    signals: list[Signal] = Field(default_factory=list)
    tools_used: list[dict[str, Any]] = Field(default_factory=list)
    model: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0


class Evidence(BaseModel, frozen=True):
    """Everything fusion folds into a verdict, besides the case and its country pack.

    Attributes:
        signals: Deterministic signals of the case.
        analyst: The analyst's read of the case, if one ran (T-09).
        manipulation_detected: Jev's flag (T-08); forces at least ``doubtful``.
    """

    signals: list[Signal] = Field(default_factory=list)
    analyst: AnalystResult | None = None
    manipulation_detected: bool = False
