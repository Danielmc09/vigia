"""Enrollment over WhatsApp: business, team, email verification, suppliers, history."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg

from vigia.domain.member import (
    Business,
    CodeCheck,
    Contact,
    Member,
    Supplier,
    normalize_contact,
)
from vigia.i18n import t
from vigia.services.commands import Command
from vigia.signals.country_pack import country_pack
from vigia.store import postgres as store
from vigia.store.crypto import Keyring, Vault

SendEmail = Callable[[str, str], Awaitable[None]]

Fields = Mapping[str, str]
_OWNER_ONLY = {"team", "supplier"}


@dataclass(frozen=True)
class Session:
    """An enrolled sender talking to VIGIA, with their business open.

    Attributes:
        conn: Connection scoped to the business.
        business: The sender's business.
        vault: Open vault of the business.
        member: The sender.
        send_email: Outbound email port ``(to, body)``.
    """

    conn: asyncpg.Connection
    business: Business
    vault: Vault
    member: Member
    send_email: SendEmail

    def reply(self, key: str, **params: str) -> str:
        """Render a reply in the business language."""
        return t(self.business.language, key, **params)


async def enroll(conn: asyncpg.Connection, keyring: Keyring, phone: str, command: Command) -> str:
    """Create a business with the sender as owner (``hola <name> <country>``).

    Args:
        conn: Connection inside a transaction.
        keyring: Process keys.
        phone: Normalized phone of the sender, not enrolled anywhere.
        command: The ``enroll`` command (``hola``).

    Returns:
        Reply text: confirmation, or how to enroll if name or country are missing.
    """
    name, country = command.fields["name"], command.fields["country"].upper()
    pack = country_pack(country) if country else None
    if not name or pack is None:
        return t(command.lang, "enroll.ask")
    business = Business(uuid4(), country, pack.language)
    vault = await store.create_business(conn, keyring, business, name)
    await store.add_member(conn, vault, "owner", Contact(phone))
    return t(business.language, "enroll.done", business=name)


async def run(session: Session, command: Command) -> str:
    """Execute a command from an enrolled member.

    Args:
        session: The sender and their open business.
        command: Parsed command (``enroll`` here means "already enrolled").

    Returns:
        Reply text in the business language.
    """
    if command.name in _OWNER_ONLY and session.member.role != "owner":
        return session.reply("command.owner_only")
    return await _HANDLERS[command.name](session, command.fields)


async def _already_enrolled(session: Session, _: Fields) -> str:
    """Reply that the sender is already enrolled."""
    return session.reply("enroll.already")


async def _help(session: Session, _: Fields) -> str:
    """Reply with the command list."""
    return session.reply("help")


async def _is_taken(session: Session, contact: str) -> bool:
    """Whether a phone or email is already enrolled in any business."""
    return await store.find_member(session.conn, session.vault.hash(contact)) is not None


async def _send_code(session: Session, member_id: UUID, email: str) -> str:
    """Issue a code for ``email`` and mail it; returns the reply for the sender.

    Capped per address and per business each hour (D-028) so VIGIA cannot be used to mail
    arbitrary addresses (incident adv-relay-correo).
    """
    email = normalize_contact(email)
    if await _is_taken(session, email):
        return session.reply("contact.taken")
    if not await store.can_issue_email_code(session.conn, session.vault.hash(email)):
        return session.reply("email.code_rate_limited")
    code = await store.issue_email_code(session.conn, session.vault, member_id, email)
    await session.send_email(email, session.reply("email.code_body", code=code))
    return session.reply("email.code_sent", email=email)


async def _team(session: Session, fields: Fields) -> str:
    """Whitelist an employee: ``+<phone> <name> [email]`` (owner only).

    The member is added even if the email code cannot go out; the reply says why.
    """
    phone, name, email = normalize_contact(fields["phone"]), fields["name"], fields["email"]
    if await _is_taken(session, phone):
        return session.reply("contact.taken")
    contact = Contact(phone, name)
    member_id = await store.add_member(session.conn, session.vault, "employee", contact)
    added = session.reply("team.added", name=name)
    if not email:
        return added
    return "\n".join([added, await _send_code(session, member_id, email)])


async def _my_email(session: Session, fields: Fields) -> str:
    """Start verification of the sender's own email."""
    return await _send_code(session, session.member.id, fields["email"])


async def _code(session: Session, fields: Fields) -> str:
    """Redeem an email verification code."""
    result = await store.redeem_email_code(
        session.conn, session.vault, session.member.id, fields["code"]
    )
    return session.reply(_CODE_REPLY[result])


async def _supplier(session: Session, fields: Fields) -> str:
    """Add a supplier: ``<name> key=value...`` (owner only)."""
    name = fields["name"]
    # Keys are free (correo=, email=, cuenta=...): a value with "@" is the email, else the account.
    values = [pair.split("=", 1)[1] for pair in fields["pairs"].split()]
    supplier = Supplier(
        name=name,
        email=next((normalize_contact(v) for v in values if "@" in v), None),
        account=next((v for v in values if "@" not in v), None),
    )
    await store.add_supplier(session.conn, session.vault, supplier)
    return session.reply("supplier.added", name=name)


async def _history(session: Session, _: Fields) -> str:
    """List the latest verdict labels of the business."""
    verdicts = await store.recent_verdicts(session.conn)
    if not verdicts:
        return session.reply("history.empty")
    lines = [
        session.reply(
            "history.line",
            date=at.date().isoformat(),
            label=session.reply(f"verdict.label.{label}"),
        )
        for at, label in verdicts
    ]
    return "\n".join([session.reply("history.header"), *lines])


_CODE_REPLY: dict[CodeCheck, str] = {
    "linked": "email.verified",
    "invalid": "email.code_invalid",
    "locked": "email.code_locked",
    "none": "email.code_none",
}

_HANDLERS: dict[str, Callable[[Session, Fields], Awaitable[str]]] = {
    "enroll": _already_enrolled,
    "team": _team,
    "my_email": _my_email,
    "code": _code,
    "supplier": _supplier,
    "history": _history,
    "help": _help,
}
