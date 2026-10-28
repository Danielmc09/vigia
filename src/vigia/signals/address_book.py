"""Address-book signals of specs/04: a known supplier from an unknown origin or account (rules).

The book arrives already resolved (decrypted by the store), so the rules stay pure.
"""

import re
from collections.abc import Sequence

from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.member import Supplier
from vigia.domain.signal import Signal
from vigia.domain.text import fold, only_digits
from vigia.signals.patterns import find_pattern
from vigia.signals.urls import registrable_domain

FREE_MAIL = frozenset(
    {"gmail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com", "icloud.com"}
)
"""Shared mail domains: a supplier on one of them is only known by its exact address."""
MIN_ACCOUNT_DIGITS = 6
ACCOUNT_CONTEXT_CHARS = 40  # D-032: an account number follows its keyword closely
_NUMBER = re.compile(r"\d[\d -]{4,}\d")


def _same_origin(sender: str, known: str) -> bool:
    """Same address, or same company domain when the known one is not a free mailbox."""
    sender, known = sender.lower(), known.lower()
    domain = registrable_domain(known.rsplit("@", 1)[-1])
    return sender == known or (
        domain not in FREE_MAIL and registrable_domain(sender.rsplit("@", 1)[-1]) == domain
    )


def _accounts(text: str, pack: CountryPack) -> set[str]:
    """Numbers that come right after an account keyword of the pack (not phones or amounts)."""
    found = set()
    for match in _NUMBER.finditer(text):
        before = text[max(0, match.start() - ACCOUNT_CONTEXT_CHARS) : match.start()]
        digits = only_digits(match.group())
        if len(digits) >= MIN_ACCOUNT_DIGITS and find_pattern(before, pack, "cuenta_bancaria"):
            found.add(digits)
    return found


def address_book_signals(
    case: Case, suppliers: Sequence[Supplier], pack: CountryPack
) -> list[Signal]:
    """Hard signals when a supplier of the book is named but the origin or account differ.

    Args:
        case: The case; its text and, for emails, the sender and display name.
        suppliers: The business address book, decrypted.
        pack: Country pack with the account keywords.

    Returns:
        ``supplier_from_unknown_origin`` and ``account_differs_from_known`` signals.
    """
    text = case.text or ""
    sender = case.email.sender if case.email else None
    named = fold(f"{text} {case.email.display_name if case.email else ''}").lower()
    signals: list[Signal] = []
    for supplier in suppliers:
        if not re.search(rf"\b{re.escape(fold(supplier.name).lower())}\b", named):
            continue
        evidence = {"supplier": supplier.name}
        if sender and supplier.email and not _same_origin(sender, supplier.email):
            signals.append(
                Signal(
                    type="supplier_from_unknown_origin",
                    weight="hard",
                    source="address_book",
                    evidence=evidence | {"sender": sender},
                )
            )
        unknown = _accounts(text, pack) - {only_digits(supplier.account or "")}
        if supplier.account and unknown:
            signals.append(
                Signal(
                    type="account_differs_from_known",
                    weight="hard",
                    source="address_book",
                    evidence=evidence | {"account": min(unknown)},
                )
            )
    return signals
