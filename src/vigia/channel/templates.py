"""WhatsApp texts built from a verdict: only i18n templates, never model text (specs/05)."""

from typing import cast, get_args

from vigia.domain.escalation import Escalation
from vigia.domain.verdict import Label, Verdict
from vigia.i18n import t

_SCAM = cast(Label, get_args(Label)[0])


def whatsapp_message(verdict: Verdict, lang: str, escalation: Escalation | None = None) -> str:
    """The reply to the member who forwarded the case.

    Args:
        verdict: The case's verdict; its ``user_explanation`` is already code-built.
        lang: Language of the business.
        escalation: Pending escalation for the owner, if the policy raised one; the
            ``confirmar`` line names its own target, and is absent without one.

    Returns:
        The message text.
    """
    lines = [verdict.user_explanation, t(lang, f"action.{verdict.action}")]
    if verdict.official_channel:
        lines.append(
            t(lang, "message.official_channel_line", official_channel=verdict.official_channel)
        )
    if escalation:
        lines.append(t(lang, "message.confirm_line", id=escalation.id, target=escalation.target))
    lines.append(t(lang, "message.id_line", case_id=verdict.case_id))
    return "\n".join(lines)


def team_alert_message(lang: str, reporter: str, kind: str, brand: str | None) -> str:
    """The short notice the rest of the team gets when a case is a scam (specs/05).

    Args:
        lang: Language of the business.
        reporter: Name of who forwarded it.
        kind: What arrived (already localized by the caller).
        brand: Brand being impersonated, if the verdict named one.

    Returns:
        The alert text (the i18n template keeps it under 200 characters).
    """
    if brand is None:
        return t(lang, f"team_alert.{_SCAM}_unbranded", reporter=reporter, kind=kind)
    return t(lang, f"team_alert.{_SCAM}", reporter=reporter, kind=kind, brand=brand)
