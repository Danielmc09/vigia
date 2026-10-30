"""WhatsApp message templates (specs/05): code-built from i18n, never model text."""

from datetime import UTC, datetime
from uuid import uuid4

from vigia.channel.templates import team_alert_message, whatsapp_message
from vigia.domain.escalation import Escalation
from vigia.domain.verdict import Verdict


def _verdict(**over: object) -> Verdict:
    base: dict[str, object] = {
        "case_id": str(uuid4()),
        "business_id": str(uuid4()),
        "label": "scam",
        "confidence": 0.9,
        "user_explanation": "Esto es una estafa.",
        "action": "dont_open_link",
        "official_channel": "Linea 01 8000 del banco",
    }
    base.update(over)
    return Verdict.model_validate(base)


def _escalation(target: str) -> Escalation:
    now = datetime.now(UTC)
    return Escalation("AB12CD", uuid4(), "report", target, now)


def test_whatsapp_message_includes_explanation_action_and_short_case_number() -> None:
    verdict = _verdict()
    msg = whatsapp_message(verdict, "es", case_ref=14)
    assert "Esto es una estafa." in msg
    assert "no abras el link" in msg
    assert msg.endswith("caso 14")
    assert verdict.case_id not in msg


def test_whatsapp_message_short_case_number_in_english() -> None:
    assert whatsapp_message(_verdict(), "en", case_ref=3).endswith("case 3")


def test_no_confirm_line_without_an_escalation() -> None:
    assert "confirmar" not in whatsapp_message(_verdict(escalation_id="AB12CD"), "es")


def test_confirm_line_names_the_escalation_target_not_the_official_channel() -> None:
    msg = whatsapp_message(_verdict(), "es", _escalation("csirt@example.org"))
    confirm_line = next(line for line in msg.splitlines() if "confirmar" in line)
    assert "confirmar AB12CD" in confirm_line
    assert "csirt@example.org" in confirm_line
    assert "Linea 01 8000" not in confirm_line


def test_team_alert_message_is_short_and_renders_in_english() -> None:
    msg = team_alert_message("en", "Ana", "bank message", "Chase")
    assert len(msg) <= 200
    assert "Chase" in msg
