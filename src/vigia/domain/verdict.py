"""Verdict labels and the full Verdict schema: the only contract fusion outputs (specs/05)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from vigia.domain.signal import Signal

Label = Literal["scam", "legit", "doubtful"]
Action = Literal[
    "ignore",
    "verify_official_channel",
    "dont_open_link",
    "dont_pay",
    "report",
    "dont_pay_sale",
    "stop_paying",
    "block_and_report",
]
"""What the member should do; the last three are the marketplace variants of specs/15."""


class Verdict(BaseModel, frozen=True):
    """The single output of a case analysis (specs/05).

    Attributes:
        case_id: Case this verdict is about.
        business_id: Business of the case.
        label: ``scam``, ``legit`` or ``doubtful``.
        confidence: How sure the system is, 0 to 1.
        signals: Every signal that went into the label, deterministic and the analyst's
            (``source == "analyst"``), hard-weighted first.
        impersonated_brand: Brand the message imitates, if any.
        user_explanation: What the member reads, built from i18n templates, never the
            model's free text (regla 14).
        action: What the member should do.
        official_channel: Where to verify instead, if ``impersonated_brand`` is known.
        requires_confirmation: True once the policy turned ``action`` into an escalation.
        escalation_id: Id of the pending escalation, if any.
        degraded: True when there was no analyst (code-only verdict).
        manipulation_detected: True when the gate (Jev) flagged the message; forces at
            least ``doubtful``, never ``legit``.
        tools_used: Analyst tool calls (``name``, ``ms``, ``usd``).
        model: Model identification used by the analyst, if any.
        total_latency_ms: End-to-end time to produce this verdict.
    """

    case_id: str
    business_id: str
    label: Label
    confidence: float = Field(ge=0, le=1)
    signals: list[Signal] = Field(default_factory=list)
    impersonated_brand: str | None = None
    user_explanation: str
    action: Action
    official_channel: str | None = None
    requires_confirmation: bool = False
    escalation_id: str | None = None
    degraded: bool = True
    manipulation_detected: bool = False
    tools_used: list[dict[str, Any]] = Field(default_factory=list)
    model: dict[str, Any] = Field(default_factory=dict)
    total_latency_ms: int = 0
