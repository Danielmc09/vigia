"""Policy of specs/05: the prompt describes, this module is what actually permits (D-045).

T-09's analyst prompt can ask for any tool; only an entry here with ``"reversible"``
in :data:`TOOL_GRANTS` runs without a confirmed escalation. A refusal is itself data,
not an exception: every gate returns a :class:`~vigia.domain.rejection.Rejection`.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Literal, get_args
from uuid import UUID, uuid4

from vigia.domain.case import Origin
from vigia.domain.escalation import Escalation, EscalationAction
from vigia.domain.rejection import Rejection, RejectionCode

Grant = Literal["reversible", "irreversible"]
ReversibleAction = Literal["reply", "alert_team", "ask_context"]
"""specs/05: these never need the owner's confirmation."""
PolicyAction = ReversibleAction | EscalationAction

REVERSIBLE_ACTIONS: tuple[ReversibleAction, ...] = get_args(ReversibleAction)

TOOL_GRANTS: dict[str, Grant] = {
    **dict.fromkeys(REVERSIBLE_ACTIONS, "reversible"),
    **dict.fromkeys(get_args(EscalationAction), "irreversible"),
}
"""Closed map: every action T-09 may ask for is one of these two grants, nothing else."""

DEFAULT_REPORT_TARGET = "csirt@vigia.local"
"""ponytail: placeholder CSIRT contact until Angel gives the real one (D-046)."""

TEAM_ALERT_COOLDOWN = timedelta(hours=6)
"""specs/05: at most one team alert per brand per business in this window."""


def _reject(code: RejectionCode, message: str) -> Rejection:
    """A non-retryable policy rejection."""
    return Rejection(error_code=code, message=message)


def new_escalation(
    action: EscalationAction, target: str, business_id: UUID, now: datetime
) -> Escalation:
    """Raise a pending escalation for an irreversible action.

    Args:
        action: What it would do.
        target: Where it would act; the reply to the owner quotes it.
        business_id: Business the action would run against.
        now: Time of the request; it expires 24 h later.

    Returns:
        A ``pending`` escalation; nothing runs until :func:`confirm` and
        :func:`may_execute` both agree.
    """
    return Escalation(
        id=uuid4().hex[:8].upper(),
        business_id=business_id,
        action=action,
        target=target,
        created_at=now,
    )


def may_execute(
    action: PolicyAction, origin: Origin, escalation: Escalation | None, now: datetime
) -> Rejection | None:
    """Whether an action is actually allowed to run right now.

    Reversible actions always may; an irreversible one needs a ``confirmed``, unexpired
    escalation for that same action, and never runs on the local or eval channel, no
    matter its status (there is no owner to confirm anything there).

    Args:
        action: Action being attempted; anything outside :data:`TOOL_GRANTS` is refused.
        origin: Channel the request came through.
        escalation: Its escalation, if it has one.
        now: Current time, for the expiry check.

    Returns:
        ``None`` if it may run, else why not.
    """
    grant = TOOL_GRANTS.get(action)
    if grant is None:
        return _reject("UNKNOWN_ACTION", f"action {action!r} is not granted")
    if grant == "reversible":
        return None
    if origin in ("local", "eval"):
        return _reject("LOCAL_CHANNEL", f"{action} never runs from {origin}")
    if escalation is None or escalation.action != action or escalation.status != "confirmed":
        return _reject("NOT_CONFIRMED", f"{action} needs a confirmed escalation")
    if escalation.is_expired(now):
        return _reject("EXPIRED", f"escalation {escalation.id} expired")
    return None


def confirm(
    escalation: Escalation, requester_is_owner: bool, now: datetime
) -> Escalation | Rejection:
    """Apply ``confirmar {id}``: only the owner, only pending, only before it expires.

    Args:
        escalation: The escalation named by the id.
        requester_is_owner: Whether whoever sent ``confirmar`` is the business owner.
        now: Current time.

    Returns:
        The escalation ``confirmed``, or why it cannot be.
    """
    rejection = _check_owner_and_pending(escalation, requester_is_owner)
    if rejection:
        return rejection
    if escalation.is_expired(now):
        return _reject("EXPIRED", f"escalation {escalation.id} expired")
    return replace(escalation, status="confirmed")


def cancel(escalation: Escalation, requester_is_owner: bool) -> Escalation | Rejection:
    """Apply ``cancelar {id}``: only the owner, and only while it is still pending.

    Args:
        escalation: The escalation named by the id.
        requester_is_owner: Whether whoever sent ``cancelar`` is the business owner.

    Returns:
        The escalation ``cancelled``, or why it cannot be.
    """
    return _check_owner_and_pending(escalation, requester_is_owner) or replace(
        escalation, status="cancelled"
    )


def _check_owner_and_pending(escalation: Escalation, requester_is_owner: bool) -> Rejection | None:
    """Shared gate of :func:`confirm` and :func:`cancel`."""
    if not requester_is_owner:
        return _reject("NOT_OWNER", "only the owner decides escalations")
    if escalation.status != "pending":
        return _reject("NOT_PENDING", f"escalation {escalation.id} is {escalation.status}")
    return None


def should_alert_team(last_alert_at: datetime | None, now: datetime) -> bool:
    """Whether a scam about a brand may alert the team again (specs/05 cooldown).

    Args:
        last_alert_at: Last team alert for the same brand and business, if any.
        now: Current time.

    Returns:
        True when there was none or :data:`TEAM_ALERT_COOLDOWN` has passed.
    """
    return last_alert_at is None or now - last_alert_at >= TEAM_ALERT_COOLDOWN
