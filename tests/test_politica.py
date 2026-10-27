"""Policy of specs/05: reversible runs, irreversible only confirmed, never locally."""

from datetime import UTC, datetime, timedelta
from typing import get_args
from uuid import uuid4

import pytest

from vigia.domain.case import Origin
from vigia.domain.escalation import Escalation, EscalationAction
from vigia.domain.rejection import Rejection
from vigia.policy.actions import (
    REVERSIBLE_ACTIONS,
    TEAM_ALERT_COOLDOWN,
    TOOL_GRANTS,
    ReversibleAction,
    cancel,
    confirm,
    may_execute,
    new_escalation,
    should_alert_team,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
BUSINESS = uuid4()


def _pending(action: EscalationAction = "report", target: str = "x") -> Escalation:
    return new_escalation(action, target, BUSINESS, NOW)


def _confirmed(action: EscalationAction = "report") -> Escalation:
    confirmed = confirm(_pending(action), requester_is_owner=True, now=NOW)
    assert isinstance(confirmed, Escalation)
    return confirmed


def test_tool_grants_is_closed() -> None:
    """Every escalation action is irreversible, the reversible ones are exactly these.

    Nothing else sneaks into the map.
    """
    assert REVERSIBLE_ACTIONS == get_args(ReversibleAction)
    assert set(TOOL_GRANTS) == set(REVERSIBLE_ACTIONS) | set(get_args(EscalationAction))
    assert all(TOOL_GRANTS[a] == "reversible" for a in REVERSIBLE_ACTIONS)
    assert all(TOOL_GRANTS[a] == "irreversible" for a in get_args(EscalationAction))


def test_unknown_action_is_a_rejection_not_a_key_error() -> None:
    rejection = may_execute("wire_money", "wazenda", None, NOW)
    assert isinstance(rejection, Rejection)
    assert rejection.error_code == "UNKNOWN_ACTION"


@pytest.mark.parametrize("action", get_args(EscalationAction))
def test_irreversible_never_runs_without_a_confirmed_escalation(action: EscalationAction) -> None:
    assert may_execute(action, "wazenda", None, NOW) == _code("NOT_CONFIRMED")
    assert may_execute(action, "wazenda", _pending(action), NOW) == _code("NOT_CONFIRMED")


@pytest.mark.parametrize("action", get_args(EscalationAction))
@pytest.mark.parametrize("origin", ["local", "eval"])
def test_local_and_eval_channel_never_execute_irreversible(
    action: EscalationAction, origin: Origin
) -> None:
    assert may_execute(action, origin, _confirmed(action), NOW) == _code("LOCAL_CHANNEL")


def test_confirmed_escalation_on_a_real_channel_may_run() -> None:
    assert may_execute("report", "wazenda", _confirmed(), NOW) is None


def test_confirmed_escalation_of_another_action_does_not_open_this_one() -> None:
    assert may_execute("delete_case", "wazenda", _confirmed("report"), NOW) == _code(
        "NOT_CONFIRMED"
    )


def test_only_the_owner_can_confirm_or_cancel() -> None:
    pending = _pending()
    assert confirm(pending, requester_is_owner=False, now=NOW) == _code("NOT_OWNER")
    assert cancel(pending, requester_is_owner=False) == _code("NOT_OWNER")
    cancelled = cancel(pending, requester_is_owner=True)
    assert isinstance(cancelled, Escalation) and cancelled.status == "cancelled"


def test_confirming_after_expiry_is_rejected() -> None:
    later = NOW + timedelta(hours=25)
    assert confirm(_pending(), requester_is_owner=True, now=later) == _code("EXPIRED")


def test_confirmed_escalation_expires_too() -> None:
    later = NOW + timedelta(hours=25)
    assert may_execute("report", "wazenda", _confirmed(), later) == _code("EXPIRED")


def test_cannot_cancel_or_confirm_what_is_no_longer_pending() -> None:
    confirmed = _confirmed()
    assert cancel(confirmed, requester_is_owner=True) == _code("NOT_PENDING")
    assert confirm(confirmed, requester_is_owner=True, now=NOW) == _code("NOT_PENDING")


@pytest.mark.parametrize("action", REVERSIBLE_ACTIONS)
@pytest.mark.parametrize("origin", ["local", "wazenda"])
def test_reversible_actions_run_without_any_escalation(
    action: ReversibleAction, origin: Origin
) -> None:
    assert may_execute(action, origin, None, NOW) is None


def test_transfer_ownership_follows_the_same_confirm_flow() -> None:
    pending = _pending("transfer_ownership", "+573000000000")
    assert may_execute("transfer_ownership", "wazenda", pending, NOW) == _code("NOT_CONFIRMED")
    confirmed = _confirmed("transfer_ownership")
    assert may_execute("transfer_ownership", "wazenda", confirmed, NOW) is None
    assert may_execute("transfer_ownership", "local", confirmed, NOW) == _code("LOCAL_CHANNEL")


def test_escalation_ids_are_short_and_distinct() -> None:
    a, b = _pending(), _pending()
    assert a.id != b.id
    assert len(a.id) <= 10


def test_should_alert_team_respects_the_six_hour_cooldown() -> None:
    assert TEAM_ALERT_COOLDOWN == timedelta(hours=6)
    assert should_alert_team(None, NOW) is True
    assert should_alert_team(NOW, NOW) is False
    assert should_alert_team(NOW - timedelta(hours=5), NOW) is False
    assert should_alert_team(NOW - timedelta(hours=6), NOW) is True


def _code(code: str) -> object:
    """Match any rejection with this error code (the log message is free)."""

    class _Matcher:
        def __eq__(self, other: object) -> bool:
            return isinstance(other, Rejection) and other.error_code == code

        def __repr__(self) -> str:
            return f"Rejection({code})"

    return _Matcher()
