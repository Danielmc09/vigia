"""Business-scoped persistence: PII goes in sealed or hashed, never in clear (specs/16).

Every function except ``find_member`` and ``record_rejection`` runs under the business
set by ``open_business``/``create_business`` (RLS on ``app.business_id``).
"""

import secrets
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from vigia.config import settings
from vigia.domain.case import Case
from vigia.domain.escalation import Escalation
from vigia.domain.member import Business, CodeCheck, Contact, Member, Role, Supplier
from vigia.domain.verdict import Label
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


async def save_case(conn: asyncpg.Connection, vault: Vault, case: Case) -> bool:
    """Store a case once per channel message id.

    Args:
        conn: Connection scoped to the business.
        vault: Open vault of the business.
        case: Normalized case.

    Returns:
        ``False`` if a case with the same ``message_id`` already exists (dedupe).
    """
    message_hash = vault.hash(case.message_id) if case.message_id else None
    status = await conn.execute(
        "INSERT INTO cases (id, business_id, kind, origin, message_id_hash, text_enc)"
        " VALUES ($1, $2, $3, $4, $5, $6)"
        " ON CONFLICT (business_id, message_id_hash) DO NOTHING",
        case.id,
        vault.business_id,
        case.kind,
        case.origin,
        message_hash,
        vault.seal("cases.text", case.text) if case.text else None,
    )
    return bool(status == "INSERT 0 1")


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
