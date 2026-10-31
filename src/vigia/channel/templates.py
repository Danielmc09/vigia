"""WhatsApp texts built from a verdict: only i18n templates, never model text (specs/05)."""

import re
from datetime import datetime
from typing import cast, get_args

from vigia.channel.panel import COLOMBIA
from vigia.domain.case import Case
from vigia.domain.escalation import Escalation
from vigia.domain.signal import by_weight
from vigia.domain.urls import defang, disarm_urls
from vigia.domain.verdict import Label, Verdict
from vigia.i18n import has_key, t

_SCAM = cast(Label, get_args(Label)[0])
# "+" and a country code, or a CO mobile (3xx); an account number like 0123-4567-8901 is neither
_PHONE = re.compile(r"\+\d(?:[ -]?\d){9,}|(?<!\d)3\d{2}[ -]?\d{3}[ -]?\d{4}(?!\d)")
# anything that reads as a host, case and TLD aside: the URL rule misses "Bit.Ly" or ".support"
# (D-108). Scheme, user part (an email's, or "bank.com@evil.click") and path go; host stays
_HOSTISH = re.compile(r"(?:\w+://)?(?:[^\s@/]+@)?((?:[\w-]+\.)+[^\W\d_]{2,})(?::\d+)?(?:[/?#]\S*)?")
# six digits or more is an account, an id or a code (D-032); "$1.500.000" is an amount, kept
_DIGITS = re.compile(r"(?<![\d$.,])\d(?:[ .-]?\d){5,}")
# only text the scammer wrote: an image's or PDF's text is Spark's (rule 14), a chat has names
_QUOTABLE = frozenset({"text", "url", "audio", "email"})
QUOTE_MAX = 120
_QUOTE_MIN_WORDS = 3
# a word of two letters or more, punctuation around it allowed; "simit-pagos[.]click" is not
_WORD = re.compile(r"\W*[^\W\d_]{2,}\W*")


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


def repeat_message(verdict: Verdict, lang: str, case_ref: int, at: datetime) -> str:
    """The reply to a forwarded copy: when it was checked, then that same verdict (D-106).

    Args:
        verdict: The stored verdict of the first copy.
        lang: Language of the business.
        case_ref: Short number of the first copy's case.
        at: When that verdict was given.

    Returns:
        The message text.
    """
    # ponytail: Colombia's clock for every business; a per-business zone when US has members.
    when = at.astimezone(COLOMBIA).strftime(t(lang, "case.repeat_when"))
    return (
        t(lang, "case.repeat", when=when)
        + "\n"
        + whatsapp_message(verdict, lang, case_ref=case_ref)
    )


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
        found.append(defang(host))
    if phone := _PHONE.search(case.text or ""):
        found.append(_masked_phone(phone))
    return found


def _masked_phone(phone: re.Match[str]) -> str:
    """A phone with its last seven digits cut: ``+57 300 ***`` or ``300 ***``."""
    head = re.sub(r"\D", "", phone.group())[:-7]
    prefix = f"+{head[:-3]} " if phone.group().startswith("+") else ""
    return f"{prefix}{head[-3:]} ***"


def _quote(case: Case) -> str | None:
    """The start of what the scammer wrote, disarmed like the indicator, or None (D-108).

    Links keep only their host and email addresses only their domain, both with dots as
    ``[.]``; phones lose their last seven digits and any other run of six digits or more
    becomes ``***``. Masked before cutting, so a cut never leaves half a number in clear.
    """
    if case.kind not in _QUOTABLE or not case.text:
        return None
    text = _HOSTISH.sub(lambda m: defang(m[1]), disarm_urls(case.text))
    text = _PHONE.sub(_masked_phone, text)
    text = " ".join(_DIGITS.sub("***", text).split())  # one line: Meta rejects multiline params
    if len(text) > QUOTE_MAX:
        text = text[: QUOTE_MAX + 1].rsplit(" ", 1)[0] + "…"
    readable = sum(bool(_WORD.fullmatch(w)) for w in text.split())
    return text if readable >= _QUOTE_MIN_WORDS else None


def _summary(lang: str, verdict: Verdict, case: Case) -> str | None:
    """What arrived as a quote, else how to spot it, else None."""
    if quote := _quote(case):
        return t(lang, "team_alert.quote", quote=quote)
    hook = _hook(lang, verdict)
    return t(lang, "team_alert.hook", hook=hook) if hook else None


def team_alert_message(lang: str, reporter: str, verdict: Verdict, case: Case) -> str:
    """The notice the rest of the team gets when a case is a scam (specs/05, T-10d).

    Enough to recognize the scam, never the scam itself: the start of what the scammer
    wrote as a quote, disarmed (D-108), or else the hook from the signals; no clickable
    link, no full number, nothing of who forwarded it or how many got the alert (D-017).

    Args:
        lang: Language of the business.
        reporter: Name of who forwarded it.
        verdict: The scam verdict; its brand and deterministic signals are used.
        case: The case, read for its kind, its text to quote, email domain and phone.

    Returns:
        The alert text.
    """
    brand = verdict.impersonated_brand
    kind = t(lang, f"team_alert.kind.{case.kind}")
    if brand is None:
        lines = [t(lang, f"team_alert.{_SCAM}_unbranded", reporter=reporter, kind=kind)]
    else:
        lines = [t(lang, f"team_alert.{_SCAM}", reporter=reporter, kind=kind, brand=brand)]
    if summary := _summary(lang, verdict, case):
        lines.append(summary)
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
        case: The case, read for its kind, text to quote and short number.

    Returns:
        The template language code and the params: business, reporter, summary, case number.
    """
    summary = _summary(lang, verdict, case) or t(
        lang, "team_alert.came_as", kind=t(lang, f"team_alert.kind.{case.kind}")
    )
    return _TEMPLATE_LANGUAGE[lang], [*names, summary, str(case.ref)]
