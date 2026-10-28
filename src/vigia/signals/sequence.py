"""Marketplace-scam signals over the ordered conversation (specs/15, rules).

A ``conversation`` case (an export with timestamps, rendered by ingest) is read as
``seller``/``buyer`` turns (:func:`vigia.domain.conversation.turns`); any other case is one
turn with no role that counts as the other party. Only buyer turns are excluded from what
"the seller" says. Nothing fires unless the text looks like a sale (``compraventa_contexto``).
"""

import re
from collections.abc import Callable
from functools import cache

from vigia.domain.case import Case
from vigia.domain.conversation import Role, turns
from vigia.domain.country import CountryPack
from vigia.domain.signal import Signal, Weight
from vigia.domain.text import amounts, fold
from vigia.signals.patterns import compile_pattern, find_pattern, pattern_matches

WEIGHTS: dict[str, Weight] = {
    "extortion": "hard",
    "staggered_charges": "hard",
    "pressure_after_payment": "hard",
    "advance_before_seeing": "medium",
    "personal_account_only": "medium",
    "verification_evasion": "medium",
    "identity_mismatch": "medium",
    "price_below_market": "medium",
    "already_paid": "light",
}
"""Every signal this module emits and its weight; ``already_paid`` only picks the reply."""

PRICE_FLOOR = 0.4
"""specs/15: a price under 40% of the pack reference is ``price_below_market``."""


_QUESTION = re.compile(r"^\s*\u00bf|\?\s*$")


@cache
def _splitter(connectors: tuple[str, ...]) -> re.Pattern[str]:
    """Clause boundaries: after punctuation, at a line break and before a pack connector."""
    before = rf"|\s+(?=(?:{'|'.join(connectors)}))" if connectors else ""
    return re.compile(rf"(?<=[.;!?,])\s+|\n{before}", re.IGNORECASE)


def _clauses(text: str, pack: CountryPack) -> list[str]:
    """``text`` cut into clauses; connectors (``conectores_clausula``) open a new one."""
    connectors = pack.patterns.get("conectores_clausula", ())
    return [c for c in _splitter(tuple(connectors)).split(text) if c.strip()]


def _price_below_market(text: str, pack: CountryPack, said: list[str]) -> Signal | None:
    """First price the seller quotes against the first pack reference the text names.

    Clauses about an advance ("con un abono de $500.000") quote the advance, not the price.
    """
    clauses = [c for line in said for c in _clauses(line, pack)]
    quoted = next(
        (
            max(found)
            for clause in clauses
            if not _has(clause, pack, "compraventa_anticipo") and (found := amounts(clause))
        ),
        None,
    )
    folded = fold(text)
    ref = next(
        (r for r in pack.price_references if compile_pattern(r.pattern).search(folded)), None
    )
    if quoted is None or ref is None or quoted >= PRICE_FLOOR * ref.price:
        return None
    evidence = {"item": ref.item, "price": quoted, "reference": ref.price}
    return _signal("price_below_market", evidence)


def _after_payment(
    chat: list[tuple[Role | None, str]], start: int | None, hit: Callable[[str], bool]
) -> bool:
    """A non-buyer turn from ``start`` on (``None``: no payment) where ``hit`` holds."""
    if start is None:
        return False
    return any(role != "buyer" and hit(said) for role, said in chat[start:])


def _has(text: str, pack: CountryPack, category: str) -> bool:
    """Whether any pattern of a pack category occurs in ``text``."""
    return find_pattern(text, pack, category) is not None


def _charge(line: str, pack: CountryPack) -> bool:
    """A charge is money asked: a ``compraventa_cobro`` item and an amount ("Seguro, …" is not)."""
    return _has(line, pack, "compraventa_cobro") and bool(amounts(line))


def _first(text: str, pack: CountryPack, *categories: str) -> int | None:
    """Earliest position in ``text`` where any pattern of ``categories`` matches."""
    found = [m.start() for c in categories for m in pattern_matches(text, pack, c)]
    return min(found, default=None)


def _offers_check(seller: str, pack: CountryPack) -> bool:
    """Whether the seller lets the buyer see or verify before paying, or takes a traceable payment.

    A visit or a test drive only clears the advance if it comes before the money is asked
    ("después puede venir a verlo" does not); a card or invoice payment is traceable.
    """
    ask = _first(seller, pack, "compraventa_anticipo", "pago_personal")
    offer = _first(seller, pack, "compraventa_verificable")
    return (
        offer is not None
        and (ask is None or offer < ask)
        and not _has(seller, pack, "compraventa_rechaza")
    ) or _has(seller, pack, "compraventa_trazable")


def _narrates(line: str, pack: CountryPack, paid: bool) -> bool:
    """Whether the buyer is speaking: money already paid, their own act or words, or a question.

    "ya le transferí", "le consigno", "le dije que lo denuncio" are the victim's, unless the
    line threatens with losing the money paid. A question is the victim's only when the
    message tells a payment (``paid``): "¿el seguro lo cobra la inmobiliaria?" after "ya pagué
    la reserva" is, "¿me consigna hoy el anticipo?" alone is the seller's ask (c-0138).
    """
    if _has(line, pack, "compraventa_presion"):
        return False
    return (
        _has(line, pack, "compraventa_pagado")
        or _has(line, pack, "compraventa_narra")
        or (paid and _QUESTION.search(line) is not None)
    )


def _plain_turns(text: str, pack: CountryPack) -> list[tuple[Role | None, str]]:
    """A loose message as clauses: the victim's narration is ``buyer``, the rest has no role."""
    paid = _has(text, pack, "compraventa_pagado")
    return [("buyer" if _narrates(c, pack, paid) else None, c) for c in _clauses(text, pack)]


def is_trade_word(word: str, pack: CountryPack) -> bool:
    """Whether a word is common in a sale ("criadero", "motos"): it stays in clear in a chat.

    Args:
        word: One word of a speaker's name.
        pack: Country pack with ``compraventa_contexto``.

    Returns:
        ``True`` when the word alone matches the sale context of the pack.
    """
    return _has(word, pack, "compraventa_contexto")


def asks_payment(line: str, pack: CountryPack) -> bool:
    """Whether a chat line asks for money: an advance, a wallet, a charge or loss pressure.

    A line where the buyer speaks (see :func:`_narrates`) never asks. Used to pick the seller.

    Args:
        line: One message of an exported chat.
        pack: Country pack with the ``compraventa_*`` patterns.

    Returns:
        ``True`` when the line demands payment.
    """
    if _narrates(line, pack, _has(line, pack, "compraventa_pagado")):
        return False
    return _charge(line, pack) or any(
        _has(line, pack, c)
        for c in ("compraventa_presion", "compraventa_anticipo", "pago_personal")
    )


def sequence_signals(case: Case, pack: CountryPack) -> list[Signal]:
    """The specs/15 sequence signals of a case, plus ``already_paid`` and ``extortion``.

    Weights: a new charge or loss pressure after a payment and threats are hard; advance
    before seeing, personal account only, price below market, verification evasion and
    identity mismatch are medium (two of them make a scam); ``already_paid`` is light.

    Args:
        case: Normalized case; its text may be a rendered chat or a loose message.
        pack: Country pack with ``compraventa_*`` patterns and price references.

    Returns:
        The signals that fired; empty when the text is not about a sale.
        A typed ``buyer:``/``seller:`` prefix outside a ``conversation`` case is plain text.
    """
    text = case.text or ""
    if not _has(text, pack, "compraventa_contexto"):
        return []
    # Roles only from an export with timestamps (ingest sets "conversation"); a typed
    # "buyer:"/"seller:" is just text and must not pick which rules run (regla 5). A loose
    # message is split into the victim's narration and what the other party demanded.
    plain = case.kind != "conversation"
    chat = _plain_turns(text, pack) if plain else turns(text)
    said = [line for role, line in chat if role != "buyer"]
    fired = _fired(chat, "\n".join(said), pack, plain)
    fired["already_paid"] = _has(text, pack, "compraventa_pagado")
    price = _price_below_market(text, pack, said)
    return [_signal(kind) for kind, hit in fired.items() if hit] + ([price] if price else [])


def _fired(
    chat: list[tuple[Role | None, str]], seller: str, pack: CountryPack, plain: bool
) -> dict[str, bool]:
    """Which sequence signals hold, given the turns and everything the non-buyer side said.

    Threats are read from ``seller`` too (every turn that is not the buyer's, as for the rest),
    so a victim telling about her own report is never extortion.
    """
    offers = _offers_check(seller, pack)
    evasion = _has(seller, pack, "compraventa_evasion")
    # specs/15 "solo acepta Nequi…": a wallet is personal-only when the seller also refuses
    # cash on delivery or avoids being checked, not merely because it is named.
    refuses = _has(seller, pack, "compraventa_rechaza") or evasion
    # An advance the victim already paid was paid before seeing only if the other side then
    # vanished or does not deliver (A27); a deposit told on its own is how honest rents go.
    paid_advance = evasion and any(
        _has(line, pack, "compraventa_pagado") and _has(line, pack, "compraventa_anticipo")
        for _, line in chat
    )
    advance = (_has(seller, pack, "compraventa_anticipo") or paid_advance) and not offers
    personal = _has(seller, pack, "pago_personal") and refuses and not offers
    # A loose message is told after the payment it mentions, so all its clauses count.
    paid = next(
        (i for i, (_, line) in enumerate(chat) if _has(line, pack, "compraventa_pagado")), None
    )
    start = 0 if plain and paid is not None else paid
    pressure = _after_payment(chat, start, lambda line: _has(line, pack, "compraventa_presion"))
    # A charge after paying (freight, SOAT, taxes) is only staggered inside the scam sequence;
    # in a loose message it must be a new demand that holds the delivery or threatens the loss.
    holds = _has(seller, pack, "compraventa_condiciona")
    scheme = (holds or pressure) if plain else (advance or pressure or holds or personal)
    return {
        "extortion": _has(seller, pack, "compraventa_amenaza"),
        "staggered_charges": _after_payment(chat, start, lambda line: _charge(line, pack))
        and scheme,
        "pressure_after_payment": pressure,
        "advance_before_seeing": advance,
        "personal_account_only": personal,
        "verification_evasion": evasion,
        "identity_mismatch": _has(seller, pack, "compraventa_identidad"),
    }


def _signal(kind: str, evidence: dict[str, object] | None = None) -> Signal:
    """A sequence signal with its weight from :data:`WEIGHTS`."""
    return Signal(type=kind, weight=WEIGHTS[kind], source="sequence", evidence=evidence or {})
