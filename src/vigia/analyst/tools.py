"""The analyst's tools (specs/03): their specs for the model and the one way to run them.

The functions behind each name are bound by :func:`vigia.services.analyst_tools.toolbox`;
this adapter only knows names, arguments and refusals. Every outcome is JSON data,
including errors (rejection as data): a tool never raises into the loop.
"""

import json
from collections.abc import Callable, Mapping
from typing import Any

from vigia.domain.analyst import ToolCall
from vigia.domain.rejection import Rejection, RejectionCode

Tool = Callable[[str, float], dict[str, Any]]
"""A bound tool: one string argument and the seconds it may take in, JSON-able dict out.

A tool that costs money says so in a ``usd`` key; the loop takes it out before the
model sees the reply (it goes to ``tools_used`` only).
"""
Tools = Mapping[str, Tool]

ACTION_TOOLS = ("alert_team", "report")
"""Actions the policy runs, never the model (specs/03, specs/05): always refused as data."""


def _spec(name: str, description: str, param: str, about: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {param: {"type": "string", "description": about}},
                "required": [param],
                "additionalProperties": False,
            },
        },
    }


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "analyze_url": _spec(
        "analyze_url",
        "Deterministic checks of one link against the country's official domains.",
        "url",
        "The link exactly as it appears in the message.",
    ),
    "check_country": _spec(
        "check_country",
        "Look up a brand or domain in the country pack: official domains and channel.",
        "query",
        "A brand name or a domain.",
    ),
    "check_address_book": _spec(
        "check_address_book",
        "Look up a supplier the business already knows, by name, email or account.",
        "query",
        "Supplier name, email or account number.",
    ),
    "web_search": _spec(
        "web_search",
        "Search the web once; use only when the country pack cannot answer.",
        "query",
        "A short search query.",
    ),
    "crop": _spec(
        "crop",
        "Find regions of the forwarded image with SAM; QR codes found are decoded to links.",
        "prompt",
        "One of: logo, qr code, button.",
    ),
    "alert_team": _spec(
        "alert_team",
        "Ask for the team to be alerted. The policy decides; you get a refusal.",
        "summary",
        "Why the team should be alerted.",
    ),
    "report": _spec(
        "report",
        "Ask to report the case to an authority; needs the owner's confirmation. Refused.",
        "target",
        "Where to report it.",
    ),
}


def reject(code: RejectionCode, message: str, retryable: bool = False) -> dict[str, Any]:
    """A rejection as the JSON the model reads.

    Args:
        code: Machine-readable reason.
        message: Short English detail for the model.
        retryable: True when the same call may work later.

    Returns:
        The dumped :class:`~vigia.domain.rejection.Rejection`.
    """
    return Rejection(error_code=code, message=message, retryable=retryable).model_dump()


def refuse_action(_: str, __: float) -> dict[str, Any]:
    """What every action tool answers: the policy acts, not the model."""
    return reject("POLICY_ONLY", "actions are decided by the policy after the verdict")


def _argument(name: str, raw: str) -> str | None:
    """The single string argument of a call, or ``None`` if the JSON is not what the spec says."""
    param = TOOL_SPECS[name]["function"]["parameters"]["required"][0]
    try:
        args = json.loads(raw)
    except ValueError:
        return None
    value = args.get(param) if isinstance(args, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def run_tool(tools: Tools, call: ToolCall, timeout_s: float) -> dict[str, Any]:
    """Run one tool call the model asked for.

    Args:
        tools: The tools bound for this case.
        call: The model's call.
        timeout_s: Seconds left in the case; tools that wait on the network honour it.

    Returns:
        The tool's JSON output, or a rejection (``POLICY_ONLY``, ``UNKNOWN_TOOL``,
        ``BAD_ARGUMENTS``) as data.
    """
    if call.name in ACTION_TOOLS:
        return refuse_action(call.arguments, timeout_s)
    if call.name not in tools or call.name not in TOOL_SPECS:
        return reject("UNKNOWN_TOOL", f"no tool named {call.name[:40]!r} is offered")
    value = _argument(call.name, call.arguments)
    if value is None:
        return reject("BAD_ARGUMENTS", "arguments must be the JSON object the tool spec describes")
    return tools[call.name](value, timeout_s)
