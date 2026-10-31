"""Business-scoped persistence: PII goes in sealed or hashed, never in clear (specs/16).

Every function except ``find_member``, ``record_rejection`` and the operator panel readers
(``panel_verdicts``, ``verdicts_today``: non-PII columns only, D-072) runs under the business
set by ``open_business``/``create_business`` (RLS on ``app.business_id``).
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID, uuid4

import asyncpg

from vigia.config import settings
from vigia.domain.case import Case, content_key
from vigia.domain.escalation import Escalation
from vigia.domain.member import Business, CodeCheck, Contact, Member, Role, Supplier
from vigia.domain.verdict import Label, Verdict, VerdictRow
from vigia.store.crypto import Encrypted, Hashed, Keyring, Vault
from vigia.store.db import set_business

CODE_TTL_MINUTES = 15  # specs/01: an email code expires in 15 minutes


async def find_member(conn: asyncpg.Connection, sender_hash: Hashed) -> Member | None:
    """Find a whitelisted member by phone hash or verified email hash, in any business.

    Args:
        conn: Connection with the application role; no business context needed.
        sender_hash: ``Keyring.hash`` of the normalized phone or email.

    Returns:
        The member, or ``None`` if the sender is not enrolled.
    """
    row = await conn.fetchrow("SELECT * FROM vigia_find_member($1)", sender_hash)
    return Member(row["member_id"], row["business_id"], row["role"]) if row else None


async def record_rejection(conn: asyncpg.Connection, sender_hash: Hashed, origin: str) -> None:
    """Log a message from a sender that is not enrolled (hash only).

    Args:
        conn: Connection with the application role.
        sender_hash: Lookup hash of the sender.
        origin: Channel it came from.
    """
    await conn.execute("SELECT vigia_record_rejection($1, $2)", sender_hash, origin)


async def create_business(
    conn: asyncpg.Connection, keyring: Keyring, business: Business, name: str
) -> Vault:
    """Store a new business with a fresh DEK and scope the transaction to it.

    Args:
        conn: Connection inside a transaction.
        keyring: Process keys; wraps the new DEK.
        business: Id, country and language of the business.
        name: Business name (stored sealed).

    Returns:
        The open vault of the business.
    """
    vault, wrapped = keyring.new_business(business.id)
    await set_business(conn, business.id)
    await conn.execute(
        "INSERT INTO businesses (id, dek_enc, name_enc, country, language)"
        " VALUES ($1, $2, $3, $4, $5)",
        business.id,
        wrapped,
        vault.seal("businesses.name", name),
        business.country,
        business.language,
    )
    return vault


async def open_business(
    conn: asyncpg.Connection, keyring: Keyring, business_id: UUID
) -> tuple[Business, Vault]:
    """Scope the transaction to a business and unwrap its DEK.

    Args:
        conn: Connection inside a transaction.
        keyring: Process keys.
        business_id: Business to open.

    Returns:
        The business and its open vault.

    Raises:
        LookupError: The business does not exist (or was deleted: its DEK is gone).
    """
    await set_business(conn, business_id)
    row = await conn.fetchrow(
        "SELECT dek_enc, country, language FROM businesses WHERE id = $1", business_id
    )
    if row is None:
        raise LookupError(f"business {business_id} not found")
    business = Business(business_id, row["country"], row["language"])
    return business, keyring.open_business(business_id, row["dek_enc"])


async def delete_business(conn: asyncpg.Connection, business_id: UUID) -> None:
    """Delete a business, its rows and its DEK (crypto-shredding, specs/16 section 2).

    Args:
        conn: Connection inside a transaction.
        business_id: Business to delete.
    """
    await set_business(conn, business_id)
    await conn.execute("DELETE FROM businesses WHERE id = $1", business_id)


async def add_member(conn: asyncpg.Connection, vault: Vault, role: Role, contact: Contact) -> UUID:
    """Whitelist a phone number in the vault's business.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        role: ``owner`` or ``employee``.
        contact: Phone (normalized) and optional name.

    Returns:
        The new member id.

    Raises:
        asyncpg.UniqueViolationError: The number is already enrolled in some business.
    """
    member_id = uuid4()
    await conn.execute(
        "INSERT INTO members (id, business_id, role, phone_hash, phone_enc, name_enc)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        member_id,
        vault.business_id,
        role,
        vault.hash(contact.phone),
        vault.seal("members.phone", contact.phone),
        vault.seal("members.name", contact.name) if contact.name else None,
    )
    return member_id


async def can_issue_email_code(conn: asyncpg.Connection, email_hash: Hashed) -> bool:
    """Whether a code may go out to this address now (D-028).

    Two caps over the last hour, counting used and expired codes: per destination
    address across every business (``settings.email_codes_per_hour``) and per business
    (``settings.email_codes_per_business_hour``).

    Args:
        conn: Connection scoped to the business.
        email_hash: ``Vault.hash`` of the normalized destination email.

    Returns:
        ``False`` once either cap is reached.
    """
    to_address = await conn.fetchval("SELECT vigia_recent_email_codes($1)", email_hash)
    in_business = await conn.fetchval(
        "SELECT count(*) FROM verification_codes WHERE created_at > now() - interval '1 hour'"
    )
    return (
        int(to_address) < settings.email_codes_per_hour
        and int(in_business) < settings.email_codes_per_business_hour
    )


async def issue_email_code(
    conn: asyncpg.Connection, vault: Vault, member_id: UUID, email: str
) -> str:
    """Create a 6-digit code that links ``email`` to the member once redeemed.

    The business's codes older than the rate-limit window are deleted here; newer ones
    are kept, even once used, because ``can_issue_email_code`` counts them.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        member_id: Member who will redeem it from WhatsApp.
        email: Normalized email to verify.

    Returns:
        The code, to send to ``email``.
    """
    await conn.execute(
        "DELETE FROM verification_codes WHERE created_at <= now() - interval '1 hour'"
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    await conn.execute(
        "INSERT INTO verification_codes"
        " (business_id, member_id, code_hash, email_hash, email_enc, expires_at)"
        " VALUES ($1, $2, $3, $4, $5, now() + make_interval(mins => $6))",
        vault.business_id,
        member_id,
        vault.hash(f"{member_id}|{code}"),
        vault.hash(email),
        vault.seal("members.email", email),
        CODE_TTL_MINUTES,
    )
    return code


async def issue_decoy_code(
    conn: asyncpg.Connection, vault: Vault, member_id: UUID | None, email: str
) -> None:
    """Store a live code row that counts for both caps but no code can ever match.

    Used when no code may go out (the email or the phone is enrolled elsewhere), so
    neither the caps nor ``codigo`` can tell a free address from a taken one (D-053,
    adv-enumeracion-tope-correo, adv-enumeracion-codigo-senuelo): its hash is random
    bytes, not the HMAC of any code, so wrong guesses, attempts, lock and TTL behave as
    for a free address whose code was mistyped.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        member_id: Member the code would be for. ``None`` when nobody was added (phone
            of another business): the row goes under the owner, born expired, because
            for a free phone the code belongs to the new employee and the owner has none.
        email: Normalized email that was asked for.
    """
    filer = member_id or await conn.fetchval("SELECT id FROM members WHERE role = 'owner'")
    code = await issue_email_code(conn, vault, filer, email)
    await conn.execute(
        "UPDATE verification_codes SET code_hash = $3,"
        " expires_at = CASE WHEN $4 THEN expires_at ELSE now() END"
        " WHERE member_id = $1 AND code_hash = $2",
        filer,
        vault.hash(f"{filer}|{code}"),
        secrets.token_bytes(32),
        member_id is not None,
    )


async def redeem_email_code(
    conn: asyncpg.Connection, vault: Vault, member_id: UUID, code: str
) -> CodeCheck:
    """Link the pending email to the member if the code is right, live and not locked.

    A wrong guess counts against every live code of the member; at
    ``settings.code_max_attempts`` those codes stop working (D-028). Redeeming expires
    all the member's codes.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        member_id: Member redeeming the code.
        code: The 6 digits they replied with.

    Returns:
        ``linked``, ``invalid`` (wrong), ``locked`` (too many wrong guesses) or ``none``
        (no live code: expired, already used or never requested).
    """
    max_attempts = settings.code_max_attempts
    row = await conn.fetchrow(
        "UPDATE verification_codes SET expires_at = now()"
        " WHERE member_id = $1 AND code_hash = $2 AND expires_at > now() AND attempts < $3"
        " RETURNING email_hash, email_enc",
        member_id,
        vault.hash(f"{member_id}|{code}"),
        max_attempts,
    )
    if row is None:
        attempts = await conn.fetchval(
            "WITH tried AS (UPDATE verification_codes SET attempts = attempts + 1"
            " WHERE member_id = $1 AND expires_at > now() RETURNING attempts)"
            " SELECT max(attempts) FROM tried",
            member_id,
        )
        if attempts is None:
            return "none"  # expired, used, or never asked for (specs/12 B3)
        return "locked" if attempts >= max_attempts else "invalid"
    await conn.execute(
        "UPDATE members SET email_hash = $2, email_enc = $3 WHERE id = $1",
        member_id,
        row["email_hash"],
        row["email_enc"],
    )
    await conn.execute(
        "UPDATE verification_codes SET expires_at = now()"
        " WHERE member_id = $1 AND expires_at > now()",
        member_id,
    )
    return "linked"


async def add_supplier(conn: asyncpg.Connection, vault: Vault, supplier: Supplier) -> None:
    """Add an entry to the business address book.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        supplier: Name, and the email and account to compare against.
    """

    def hashed(value: str | None) -> Hashed | None:
        """Lookup hash, or ``None`` for a missing value."""
        return vault.hash(value) if value else None

    def sealed(column: str, value: str | None) -> Encrypted | None:
        """Sealed value, or ``None`` for a missing value."""
        return vault.seal(column, value) if value else None

    await conn.execute(
        "INSERT INTO suppliers (business_id, name_enc, email_hash, email_enc, account_hash,"
        " account_enc) VALUES ($1, $2, $3, $4, $5, $6)",
        vault.business_id,
        vault.seal("suppliers.name", supplier.name),
        hashed(supplier.email),
        sealed("suppliers.email", supplier.email),
        hashed(supplier.account),
        sealed("suppliers.account", supplier.account),
    )


async def save_case(conn: asyncpg.Connection, vault: Vault, case: Case) -> int | None:
    """Store a case once per channel message id, with the next number of its business.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        case: Normalized case.

    Returns:
        The case number the member sees (D-091), or ``None`` if a case with the same
        ``message_id`` already exists (dedupe).
    """
    message_hash = vault.hash(case.message_id) if case.message_id else None
    content = content_key(case)
    # ponytail: a duplicate still bumps the counter (a gap in the numbers, harmless); the
    # business row stays locked until commit, so one business numbers its cases one by one.
    ref: int | None = await conn.fetchval(
        "WITH n AS (UPDATE businesses SET case_seq = case_seq + 1 WHERE id = $2"
        " RETURNING case_seq)"
        " INSERT INTO cases (id, business_id, kind, origin, message_id_hash, text_enc, ref,"
        " content_hash) SELECT $1, $2, $3, $4, $5, $6, n.case_seq, $7 FROM n"
        " ON CONFLICT (business_id, message_id_hash) DO NOTHING RETURNING ref",
        case.id,
        vault.business_id,
        case.kind,
        case.origin,
        message_hash,
        vault.seal("cases.text", case.text) if case.text else None,
        _content_hash(vault, content) if content else None,
    )
    return ref


def _content_hash(vault: Vault, key: str) -> Hashed:
    """Content hash scoped to the business (D-083, D-106): no correlation across businesses."""
    return vault.hash(f"{vault.business_id}:{key}")


class Seen(NamedTuple):
    """A verdict already given for the same content.

    Attributes:
        ref: Case number the member saw.
        at: When the verdict was given.
        verdict: The verdict, decrypted.
    """

    ref: int
    at: datetime
    verdict: Verdict


async def fresh_verdict(
    conn: asyncpg.Connection, vault: Vault, key: str, max_age: timedelta
) -> Seen | None:
    """The newest verdict for the same content, if younger than ``max_age`` (D-106).

    Takes a transaction lock on the content first, so a copy arriving while the original is
    still being judged waits for its verdict instead of paying the models again.

    Args:
        conn: Connection scoped to the business, inside a transaction.
        vault: Open vault of the business.
        key: :func:`~vigia.domain.case.content_key` of the case.
        max_age: Oldest verdict worth repeating.

    Returns:
        The verdict with its case number and time, or ``None`` to analyze it again.
    """
    content = _content_hash(vault, key)
    await conn.execute(
        "SELECT pg_advisory_xact_lock($1)", int.from_bytes(content[:8], "big", signed=True)
    )
    row = await conn.fetchrow(
        "SELECT c.ref, v.created_at, v.payload_enc FROM cases c"
        " JOIN verdicts v ON v.case_id = c.id"
        " WHERE c.business_id = $1 AND c.content_hash = $2 AND c.ref IS NOT NULL"
        " AND v.created_at > now() - $3::interval"
        " ORDER BY v.created_at DESC LIMIT 1",
        vault.business_id,
        content,
        max_age,
    )
    if row is None:
        return None
    payload = vault.open("verdicts.payload", row["payload_enc"])
    return Seen(row["ref"], row["created_at"], Verdict.model_validate_json(payload))


async def recent_verdicts(conn: asyncpg.Connection, limit: int = 5) -> list[tuple[datetime, Label]]:
    """Latest verdict labels of the business in context, newest first.

    Args:
        conn: Connection scoped to the business.
        limit: How many to return.

    Returns:
        ``(created_at, label)`` pairs.
    """
    rows = await conn.fetch(
        "SELECT created_at, label FROM verdicts ORDER BY created_at DESC LIMIT $1", limit
    )
    return [(r["created_at"], r["label"]) for r in rows]


async def save_verdict(
    conn: asyncpg.Connection, vault: Vault, case: Case, verdict: Verdict
) -> None:
    """Store a verdict: the whole of it sealed, plus the non-PII columns the panel reads.

    Only deterministic signal types go in clear; the analyst's stay sealed (D-060, D-072).

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        case: The case the verdict is about (already saved).
        verdict: The verdict.
    """
    row = VerdictRow.of(verdict, case.kind, datetime.now(UTC))
    await conn.execute(
        "INSERT INTO verdicts (business_id, case_id, label, payload_enc, kind, brand, confidence,"
        " signal_types, tools, usd, latency_ms)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        vault.business_id,
        case.id,
        verdict.label,
        vault.seal("verdicts.payload", verdict.model_dump_json()),
        row.kind,
        row.brand,
        row.confidence,
        row.signal_types,
        row.tools,
        row.usd,
        row.latency_ms,
    )


async def panel_verdicts(conn: asyncpg.Connection, limit: int) -> list[VerdictRow]:
    """Latest verdicts of every business, without PII, for the operator panel (D-072).

    Args:
        conn: Any application connection; the definer function crosses businesses.
        limit: How many to return (the function caps it at 200).

    Returns:
        Rows newest first.
    """
    rows = await conn.fetch("SELECT * FROM vigia_panel_verdicts($1)", limit)
    return [VerdictRow(**dict(row)) for row in rows]


async def verdicts_today(conn: asyncpg.Connection) -> dict[str, int]:
    """Verdicts of every business since midnight in Colombia, by label (D-072).

    Args:
        conn: Any application connection.

    Returns:
        Count per label; labels with no verdict today are absent.
    """
    rows = await conn.fetch("SELECT label, total FROM vigia_panel_today()")
    return {row["label"]: row["total"] for row in rows}


async def transfer_ownership(conn: asyncpg.Connection, owner_id: UUID, new_owner_id: UUID) -> None:
    """Swap roles: the new owner takes the business, the old one stays as employee (B7).

    Args:
        conn: Connection scoped to the business; RLS keeps both ids inside it.
        owner_id: Current owner.
        new_owner_id: Member of the same business who becomes the owner.
    """
    await conn.execute(
        "UPDATE members SET role = CASE WHEN id = $2 THEN 'owner' ELSE 'employee' END"
        " WHERE id IN ($1, $2)",
        owner_id,
        new_owner_id,
    )


async def save_escalation(conn: asyncpg.Connection, vault: Vault, escalation: Escalation) -> None:
    """Store a new escalation, or its new status if it already exists.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business; seals the target.
        escalation: The escalation as the policy left it.
    """
    await conn.execute(
        "INSERT INTO escalations (id, business_id, action, target_enc, status, created_at)"
        " VALUES ($1, $2, $3, $4, $5, $6)"
        " ON CONFLICT (business_id, id) DO UPDATE SET status = EXCLUDED.status",
        escalation.id,
        vault.business_id,
        escalation.action,
        vault.seal("escalations.target", escalation.target),
        escalation.status,
        escalation.created_at,
    )


async def find_escalation(
    conn: asyncpg.Connection, vault: Vault, escalation_id: str
) -> Escalation | None:
    """Escalation of the business in context by its short id.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business; opens the target.
        escalation_id: Id as quoted to the owner.

    Returns:
        The escalation, or ``None`` if this business has none with that id.
    """
    row = await conn.fetchrow(
        "SELECT action, target_enc, status, created_at FROM escalations WHERE id = $1",
        escalation_id,
    )
    if row is None:
        return None
    return Escalation(
        id=escalation_id,
        business_id=vault.business_id,
        action=row["action"],
        target=vault.open("escalations.target", row["target_enc"]),
        created_at=row["created_at"],
        status=row["status"],
    )


async def first_webhook_event(conn: asyncpg.Connection, id_hash: Hashed) -> bool:
    """Remember a channel message id; only its first delivery returns ``True`` (specs/01).

    Args:
        conn: Any application connection; the definer function needs no business.
        id_hash: ``Keyring.hash`` of ``"<origin>:<message_id>"`` (never the id in clear).

    Returns:
        ``False`` when the id was already seen (a webhook retry).
    """
    return bool(await conn.fetchval("SELECT vigia_webhook_first($1)", id_hash))


async def team(conn: asyncpg.Connection, vault: Vault) -> list[Contact]:
    """Every member of the business in context, phone and name opened.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.

    Returns:
        Their contacts, oldest member first.
    """
    rows = await conn.fetch(
        "SELECT phone_enc, name_enc, last_inbound_at FROM members"
        " WHERE business_id = $1 ORDER BY created_at",
        vault.business_id,
    )
    return [
        Contact(
            vault.open("members.phone", r["phone_enc"]),
            vault.open("members.name", r["name_enc"]) if r["name_enc"] else None,
            r["last_inbound_at"],
        )
        for r in rows
    ]


async def touch_member(conn: asyncpg.Connection, sender_hash: Hashed) -> None:
    """Record that a member just wrote: Meta's 24 h window opens again (D-097).

    Args:
        conn: Connection scoped to the member's business.
        sender_hash: Keyed hash of the member's phone.
    """
    await conn.execute(
        "UPDATE members SET last_inbound_at = now() WHERE phone_hash = $1", sender_hash
    )


async def business_name(conn: asyncpg.Connection, vault: Vault) -> str:
    """The name of the business in context, opened.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.

    Returns:
        The name the owner enrolled with.
    """
    sealed = await conn.fetchval("SELECT name_enc FROM businesses WHERE id = $1", vault.business_id)
    return vault.open("businesses.name", sealed)


async def save_alert_rejections(conn: asyncpg.Connection, case_id: UUID, codes: list[str]) -> None:
    """Keep on the verdict why some team alerts could not go out, for the panel.

    Args:
        conn: Connection scoped to the business.
        case_id: The case whose verdict it is.
        codes: Rejection codes, one per member left without alert.
    """
    await conn.execute(
        "UPDATE verdicts SET alert_rejections = $2 WHERE case_id = $1", case_id, codes
    )


async def suppliers(conn: asyncpg.Connection, vault: Vault) -> list[Supplier]:
    """The address book of the business in context, opened for the analyst.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.

    Returns:
        The suppliers.
    """
    rows = await conn.fetch("SELECT name_enc, email_enc, account_enc FROM suppliers")

    def opened(column: str, blob: bytes | None) -> str | None:
        """Opened value, or ``None`` for a missing one."""
        return vault.open(column, blob) if blob else None

    return [
        Supplier(
            vault.open("suppliers.name", r["name_enc"]),
            opened("suppliers.email", r["email_enc"]),
            opened("suppliers.account", r["account_enc"]),
        )
        for r in rows
    ]


async def last_verdict_at(
    conn: asyncpg.Connection, label: Label, brand: str | None
) -> datetime | None:
    """When the business in context last got this label for this brand (alert cooldown).

    Args:
        conn: Connection scoped to the business.
        label: Verdict label.
        brand: Impersonated brand; ``None`` matches the verdicts without one.

    Returns:
        The latest such verdict time, if any.
    """
    found: datetime | None = await conn.fetchval(
        "SELECT max(created_at) FROM verdicts WHERE label = $1 AND brand IS NOT DISTINCT FROM $2",
        label,
        brand,
    )
    return found


async def case_text(
    conn: asyncpg.Connection, vault: Vault, ref: int
) -> tuple[str, str | None] | None:
    """Text of a case of the business in context and the brand its verdict named.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        ref: Case number, as the reply quoted it (D-091).

    Returns:
        ``(text, brand)``; ``None`` when the case is not of this business.
    """
    row = await conn.fetchrow(
        "SELECT c.text_enc, v.brand FROM cases c LEFT JOIN verdicts v ON v.case_id = c.id"
        " WHERE c.ref = $1 ORDER BY v.created_at DESC LIMIT 1",
        ref,
    )
    if row is None:
        return None
    text = vault.open("cases.text", row["text_enc"]) if row["text_enc"] else ""
    return text, row["brand"]
