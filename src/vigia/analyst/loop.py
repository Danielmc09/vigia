"""The analyst's own tool loop (adapter, specs/03): Spark investigates, then fills the schema.

Limits are the spec's: at most ``MAX_STEPS`` model turns, the one retry of an invalid
answer included, and ``DEADLINE_S`` seconds per case, tools included (D-060). Anything
else (model down, limits hit, two invalid answers) returns ``None``: the verdict is then
degraded, never invented.

Regla 14: the model's text is only ever parsed as the schema answer. Prose next to tool
calls is dropped, not even sent back to the model, and its ``reasoning`` only reaches logs.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any, get_args

from pydantic import BaseModel, Field, ValidationError

from vigia.analyst.tools import TOOL_SPECS, Tools, run_tool
from vigia.domain.analyst import AnalystResult, Evidence, ModelRequest, ModelTurn
from vigia.domain.case import Case
from vigia.domain.rejection import Rejection
from vigia.domain.signal import Signal
from vigia.domain.text import mail_domain
from vigia.domain.verdict import Label

MAX_STEPS = 6  # specs/03
DEADLINE_S = 25  # specs/03
MAX_ANALYST_SIGNALS = 5
MAX_TOOL_CALLS = 4  # per turn, D-060: extra calls in one turn are dropped
SIGNAL_NAME = re.compile(r"[a-z_]{1,40}")
PROMPT = (Path(__file__).parent / "prompts" / "analyst.md").read_text("utf-8")
RETRY = "Invalid answer ({error}). Reply with only the Veredicto JSON object."
UNKNOWN_TOOL = "unknown_tool"  # regla 14: a name the model made up is never recorded

Spark = Callable[[ModelRequest], ModelTurn | Rejection]
"""Port to the model: one request, one turn or a rejection (see ``model/spark.py``)."""


class _Answer(BaseModel, extra="forbid"):
    """The ``Veredicto`` the model must end with."""

    label: Label
    confidence: float = Field(ge=0, le=1)
    signals: list[str]
    reasoning: str


def output_schema() -> dict[str, Any]:
    """The strict JSON schema of the final answer, as the model API wants it.

    Returns:
        A schema with every property required and no extras (strict mode).
    """
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": list(get_args(Label))},
            "confidence": {"type": "number"},
            "signals": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
        },
        "required": ["label", "confidence", "signals", "reasoning"],
        "additionalProperties": False,
    }


def _brief(case: Case, evidence: Evidence) -> str:
    """The case as data for the model: never instructions, always a JSON document."""
    brief = {
        "country": case.country,
        "kind": case.kind,
        "text": case.text,
        "urls": case.urls,
        "qr_urls": case.qr_urls,
        "email": (
            {
                "sender_domain": mail_domain(case.email.sender),
                "reply_to_domain": mail_domain(case.email.reply_to),
                "with_headers": case.email.with_headers,
            }
            if case.email
            else None
        ),
        "signals": [s.model_dump(mode="json") for s in evidence.signals],
        "manipulation_detected": evidence.manipulation_detected,
    }
    return json.dumps(brief, ensure_ascii=False)


def _parse(content: str | None) -> tuple[_Answer | None, str]:
    """The schema answer, or why it is not one (without echoing the model's text)."""
    try:
        return _Answer.model_validate_json(content or ""), ""
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        return None, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors)


def _signals(names: list[str]) -> list[Signal]:
    """The analyst's signal names as light evidence; anything not an id is dropped."""
    valid = [n for n in dict.fromkeys(names) if SIGNAL_NAME.fullmatch(n)]
    return [Signal(type=n, weight="light", source="analyst") for n in valid[:MAX_ANALYST_SIGNALS]]


def _run_calls(
    turn: ModelTurn, tools: Tools, used: list[dict[str, Any]], deadline: float
) -> list[dict[str, Any]] | None:
    """Run the turn's tool calls; the messages to append (assistant, then tools).

    Returns ``None`` when the case runs out of time before a call: each tool gets only
    what is left of ``deadline`` (a ``monotonic`` instant).
    """
    calls = turn.tool_calls[:MAX_TOOL_CALLS]
    assistant = {
        "role": "assistant",
        "content": None,  # regla 14: prose next to tool calls is dropped
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in calls
        ],
    }
    replies: list[dict[str, Any]] = [assistant]
    for call in calls:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        start = perf_counter()
        output = run_tool(tools, call, remaining)
        usd = float(output.pop("usd", 0.0))  # billing, not the model's business
        name = call.name if call.name in TOOL_SPECS else UNKNOWN_TOOL
        used.append({"name": name, "ms": round((perf_counter() - start) * 1000), "usd": usd})
        content = json.dumps(output, ensure_ascii=False)
        replies.append({"role": "tool", "tool_call_id": call.id, "content": content})
    return replies


def _result(answer: _Answer, used: list[dict[str, Any]], clock: float) -> AnalystResult:
    """The analyst's read from a valid answer; the last model entry in ``used`` names it."""
    return AnalystResult(
        label=answer.label,
        confidence=answer.confidence,
        explanation=answer.reasoning,
        signals=_signals(answer.signals),
        tools_used=used,
        model={"name": used[-1]["name"]},
        latency_ms=round((perf_counter() - clock) * 1000),
    )


def investigate(case: Case, evidence: Evidence, tools: Tools, spark: Spark) -> AnalystResult | None:
    """Let the model investigate the case with the bound tools and answer the schema.

    Args:
        case: The case.
        evidence: The deterministic signals and Jev's flag, sent as part of the brief.
        tools: Tools bound for this case (see ``services/analyst_tools.toolbox``).
        spark: The model port.

    Returns:
        The analyst's read, or ``None`` when the model failed, ran out of steps or time,
        or gave two invalid answers.
    """
    deadline, clock = monotonic() + DEADLINE_S, perf_counter()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": _brief(case, evidence)},
    ]
    used: list[dict[str, Any]] = []
    retried = False
    for step in range(MAX_STEPS):  # specs/03: the retry is one of the six
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        offer = [TOOL_SPECS[name] for name in tools] if step < MAX_STEPS - 1 else []
        request = ModelRequest(
            messages=messages, tools=offer, output_schema=output_schema(), timeout_s=remaining
        )
        sent = perf_counter()
        turn = spark(request)
        if isinstance(turn, Rejection):
            return None
        ms = round((perf_counter() - sent) * 1000)
        used.append({"name": turn.model, "ms": ms, "usd": turn.usd})
        if turn.tool_calls and offer:
            replies = _run_calls(turn, tools, used, deadline)
            if replies is None:
                return None
            messages = messages + replies
            continue
        answer, error = _parse(turn.content)
        if answer is not None:
            return _result(answer, used, clock)
        if retried:
            return None
        retried = True
        messages = messages + [{"role": "user", "content": RETRY.format(error=error)}]
    return None
