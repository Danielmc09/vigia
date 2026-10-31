"""The analyst's own read of a case, before fusion folds it with deterministic signals.

T-09 builds the real tool-using loop that produces this; T-07 only needs the contract so
fusion and the endpoint can be tested against a mock (domain).
"""

from typing import Any

from pydantic import BaseModel, Field

from vigia.domain.signal import Signal
from vigia.domain.urls import Unrolled
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
        links: Short links the pipeline already unrolled, by original URL; the analyst
            reads them and never fetches one itself (D-101).
    """

    signals: list[Signal] = Field(default_factory=list)
    analyst: AnalystResult | None = None
    manipulation_detected: bool = False
    links: dict[str, Unrolled] = Field(default_factory=dict)


class ToolCall(BaseModel, frozen=True):
    """One tool the model asked to run.

    Attributes:
        id: The model's id for the call; the tool reply must quote it.
        name: Tool name, as offered in :class:`ModelRequest` ``tools``.
        arguments: The raw JSON arguments, unparsed: parsing is the tool runner's job.
    """

    id: str
    name: str
    arguments: str


class ModelRequest(BaseModel, frozen=True):
    """One turn asked of the model: the conversation so far, the tools and the output schema.

    Attributes:
        messages: Chat messages (``role``/``content``...), system prompt first.
        tools: Tool specs offered this turn; empty means the model must answer.
        output_schema: JSON schema the final answer must follow (strict).
        timeout_s: Seconds this turn may take.
        output_name: Name of the output schema sent to the provider.
        max_tokens: Cap on the completion tokens, if any.
    """

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    output_schema: dict[str, Any]
    timeout_s: float = Field(gt=0)
    output_name: str = "Veredicto"
    max_tokens: int | None = Field(default=None, gt=0)


class ModelTurn(BaseModel, frozen=True):
    """What the model answered to a :class:`ModelRequest`.

    Attributes:
        content: Its text; only ever parsed as the schema answer, never shown (regla 14).
        tool_calls: Tools it asked to run, if any.
        usd: Cost of this turn.
        tokens: Total tokens of this turn.
        model: Model id that answered.
    """

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usd: float = 0.0
    tokens: int = 0
    model: str
