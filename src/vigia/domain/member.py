"""Businesses, their members and suppliers, and how contacts are normalized (domain)."""

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

Role = Literal["owner", "employee"]


@dataclass(frozen=True)
class Business:
    """An enrolled business.

    Attributes:
        id: Business id; the RLS key.
        country: ISO code of its country pack.
        language: Language of every reply to its members.
    """

    id: UUID
    country: str
    language: str


@dataclass(frozen=True)
class Member:
    """A whitelisted sender, as found by its phone or verified email hash.

    Attributes:
        id: Member id.
        business_id: Business it belongs to.
        role: ``owner`` approves irreversible actions; ``employee`` only forwards.
    """

    id: UUID
    business_id: UUID
    role: Role


@dataclass(frozen=True)
class Contact:
    """Personal data of a member to enroll; stored encrypted and hashed, never in clear.

    Attributes:
        phone: E.164 phone number.
        name: Display name, if known.
    """

    phone: str
    name: str | None = None


@dataclass(frozen=True)
class Supplier:
    """An entry of the business address book (specs/04).

    Attributes:
        name: Supplier name.
        email: Known payment email, if any.
        account: Known bank account, if any.
    """

    name: str
    email: str | None = None
    account: str | None = None


CodeCheck = Literal["linked", "invalid", "locked", "none"]
"""Outcome of redeeming an email code: linked, wrong, too many wrong tries, or no live code."""


def normalize_phone(raw: str) -> str:
    """Normalize a phone number to ``+`` and digits, the form that gets hashed.

    Args:
        raw: Number as typed or as sent by the channel (``+57 300 000 0000``, ``573000000000``).

    Returns:
        The number as ``+<digits>``.
    """
    return "+" + re.sub(r"\D", "", raw)


def normalize_contact(raw: str) -> str:
    """Normalize a sender identifier: emails lowercased, phones to ``+<digits>``.

    Args:
        raw: Phone number or email address.

    Returns:
        The canonical form used for lookup hashes.
    """
    raw = raw.strip()
    return raw.lower() if "@" in raw else normalize_phone(raw)
