"""Verdict labels and the full Verdict schema: the only contract fusion outputs (specs/05)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
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

    @property
    def usd(self) -> float:
        """Model spend of the case: the sum of its tool calls' ``usd``."""
        return tools_usd(self.tools_used)


def tools_usd(tools_used: Sequence[Mapping[str, Any]]) -> float:
    """The sum of the ``usd`` of some tool calls (``None`` counts as 0).

    Args:
        tools_used: ``Verdict.tools_used`` or ``AnalystResult.tools_used``.

    Returns:
        USD.
    """
    return sum(float(t.get("usd") or 0) for t in tools_used)


class VerdictRow(BaseModel, frozen=True):
    """One verdict as the operator panel sees it: no PII, no business (D-072).

    Attributes:
        created_at: When the verdict was stored.
        kind: Case kind (``text``, ``pdf``...), if recorded.
        brand: Impersonated brand from the country pack, if any.
        label: ``scam``, ``legit`` or ``doubtful``.
        confidence: Verdict confidence, if recorded.
        signal_types: Deterministic signal types only; the analyst's never (D-060).
        tools: Distinct tool and model names the case used.
        usd: Spend of the case.
        latency_ms: End-to-end time of the case.
        alert_rejections: Why a team alert could not go out (``TEMPLATE_PENDING``).
    """

    created_at: datetime
    kind: str | None = None
    brand: str | None = None
    label: Label
    confidence: float | None = None
    signal_types: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    usd: float = 0.0
    latency_ms: int = 0
    alert_rejections: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, verdict: Verdict, kind: str | None, created_at: datetime) -> "VerdictRow":
        """The panel's view of a verdict; analyst signal types stay out (D-060, D-072).

        Args:
            verdict: The full verdict.
            kind: Kind of the case it judged.
            created_at: When it was produced or stored.

        Returns:
            The row, with no PII by construction.
        """
        return cls(
            created_at=created_at,
            kind=kind,
            brand=verdict.impersonated_brand,
            label=verdict.label,
            confidence=verdict.confidence,
            signal_types=list(
                dict.fromkeys(s.type for s in verdict.signals if s.source != "analyst")
            ),
            tools=list(dict.fromkeys(str(tool.get("name")) for tool in verdict.tools_used)),
            usd=verdict.usd,
            latency_ms=verdict.total_latency_ms,
        )
