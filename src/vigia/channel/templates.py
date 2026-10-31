"""WhatsApp texts built from a verdict: only i18n templates, never model text (specs/05)."""

import re
from typing import cast, get_args

from vigia.domain.case import Case
from vigia.domain.escalation import Escalation
from vigia.domain.signal import by_weight
from vigia.domain.verdict import Label, Verdict
from vigia.i18n import has_key, t

_SCAM = cast(Label, get_args(Label)[0])
# "+" and a country code, or a CO mobile (3xx); an account number like 0123-4567-8901 is neither
_PHONE = re.compile(r"\+\d(?:[ -]?\d){9,}|(?<!\d)3\d{2}[ -]?\d{3}[ -]?\d{4}(?!\d)")


def whatsapp_message(
    verdict: Verdict,
    lang: str,
    escalation: Escalation | None = None,
    *,
    case_ref: int | None = None,
) -> str:
    """The reply to the member who forwarded the case.

    Args:
        verdict: The case's verdict; its ``user_explanation`` is already code-built.
        lang: Language of the business.
        escalation: Pending escalation for the owner, if the policy raised one; the
            ``confirmar`` line names its own target, and is absent without one.
        case_ref: Short case number (D-091); the UUID never reaches the member, so a case
            that was never stored gets no id line.

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
    if case_ref is not None:
        lines.append(t(lang, "message.id_line", ref=str(case_ref)))
    return "\n".join(lines)


def _hook(lang: str, verdict: Verdict) -> str | None:
    """The strongest deterministic signal, in the catalog's words; never the analyst's (D-065)."""
    known = [
        s
        for s in by_weight(verdict.signals)
        if s.source != "analyst"
        and s.type != "official_domain"
        and has_key(lang, f"signal.{s.type}")
    ]
    return t(lang, f"signal.{known[0].type}") if known else None


def _indicators(verdict: Verdict, case: Case) -> list[str]:
    """Where the scam comes from, disarmed: dots as ``[.]``, the last seven digits cut."""
    found = []
    # the domain a link signal convicted, never the official one the scam also links to
    official = {s.evidence.get("domain") for s in verdict.signals if s.type == "official_domain"}
    host = next(
        (
            s.evidence["domain"]
            for s in by_weight(verdict.signals)
            if s.source in ("urls", "qr") and s.evidence.get("domain") not in official | {None}
        ),
        None,
    )
    if host is None and case.email and case.email.sender:
        host = case.email.sender.rpartition("@")[2]
    if host:
        found.append(host.replace(".", "[.]"))
    if phone := _PHONE.search(case.text or ""):
        head = re.sub(r"\D", "", phone.group())[:-7]
        prefix = f"+{head[:-3]} " if phone.group().startswith("+") else ""
        found.append(f"{prefix}{head[-3:]} ***")
    return found


def team_alert_message(lang: str, reporter: str, verdict: Verdict, case: Case) -> str:
    """The notice the rest of the team gets when a case is a scam (specs/05, T-10d).

    Enough to recognize the scam, never the scam itself: the hook comes from the signals
    and the indicator is disarmed; no original text, no clickable link, no number of who
    forwarded it or how many got the alert (D-017).

    Args:
        lang: Language of the business.
        reporter: Name of who forwarded it.
        verdict: The scam verdict; its brand and deterministic signals are used.
        case: The case, read only for its kind, email domain and phone prefix.

    Returns:
        The alert text.
    """
    brand = verdict.impersonated_brand
    kind = t(lang, f"team_alert.kind.{case.kind}")
    if brand is None:
        lines = [t(lang, f"team_alert.{_SCAM}_unbranded", reporter=reporter, kind=kind)]
    else:
        lines = [t(lang, f"team_alert.{_SCAM}", reporter=reporter, kind=kind, brand=brand)]
    hook = _hook(lang, verdict)
    if hook:
        lines.append(t(lang, "team_alert.hook", hook=hook))
    indicators = _indicators(verdict, case)
    if indicators:
        lines.append(
            t(lang, "team_alert.indicator", indicator=" · ".join(indicators))
            + " "
            + t(lang, "team_alert.disarmed")
        )
    return "\n".join(lines)


# ponytail: only es/en exist today; a new lang in i18n needs an entry here too (KeyError otherwise).
_TEMPLATE_LANGUAGE = {"es": "es", "en": "en_US"}


def team_alert_params(
    lang: str, names: tuple[str, str], verdict: Verdict, case: Case
) -> tuple[str, list[str]]:
    """Body params of the approved template ``alerta_equipo_estafa`` (T-18b, D-097).

    Meta rejects empty or multiline params, so each one is a single code-built line.

    Args:
        lang: Language of the business.
        names: Business name and the name of who forwarded it.
        verdict: The scam verdict; only its deterministic hook is used.
        case: The case, read for its kind and short number.

    Returns:
        The template language code and the params: business, reporter, summary, case number.
    """
    hook = _hook(lang, verdict)
    if hook:
        summary = t(lang, "team_alert.hook", hook=hook)
    else:
        summary = t(lang, "team_alert.came_as", kind=t(lang, f"team_alert.kind.{case.kind}"))
    return _TEMPLATE_LANGUAGE[lang], [*names, summary, str(case.ref)]
