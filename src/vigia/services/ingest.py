"""Entry of every incoming message: whitelist, commands, normalization to Case, dedupe.

Order (specs/01, specs/02): unknown senders are dropped (only ``hola`` enrolls), commands
are answered without creating a case, everything else becomes a ``Case`` stored once per
channel message id and handed to analysis.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from functools import partial
from typing import TypeGuard
from uuid import uuid4

import asyncpg

from vigia import telemetry
from vigia.domain.case import Case, CaseKind, EmailMeta, IncomingMessage
from vigia.domain.conversation import never, parse_chat, render_chat
from vigia.domain.member import Business, normalize_contact
from vigia.domain.text import clean_text
from vigia.domain.urls import extract_urls
from vigia.i18n import t
from vigia.services import enrollment
from vigia.services.commands import Command, misused_command, parse_command
from vigia.signals.country_pack import country_pack
from vigia.signals.sequence import asks_payment, is_trade_word
from vigia.store import postgres as store
from vigia.store.crypto import Keyring


@dataclass(frozen=True)
class Ports:
    """What ingestion needs from outside.

    Attributes:
        keyring: Process keys (KEK and lookup-hash key).
        send_email: Sends ``(to, body)``; used for verification codes.
        analyze: Receives each new case; the pipeline after ingestion.
    """

    keyring: Keyring
    send_email: enrollment.SendEmail
    analyze: Callable[[Case], Awaitable[None]]


async def handle(conn: asyncpg.Connection, ports: Ports, msg: IncomingMessage) -> str | None:
    """Process one incoming message.

    Args:
        conn: Connection with the application role, inside a transaction.
        ports: Keys and outbound ports.
        msg: Message as parsed by the channel adapter.

    Returns:
        Text to reply to the sender, or ``None`` when there is nothing to say now
        (sender not enrolled, or a new case handed to analysis). Text without a letter or
        digit (empty, emoji, "?") gets a short help and creates no case.
    """
    sender = normalize_contact(msg.sender)
    sender_hash = ports.keyring.hash(sender)
    command = parse_command(msg.text) if msg.kind == "text" else None
    member = await store.find_member(conn, sender_hash)
    if member is None:
        if command and command.name == "enroll" and msg.origin != "email":
            reply = await enrollment.enroll(conn, ports.keyring, sender, command)
            await store.touch_member(conn, sender_hash)  # the owner just wrote: window open
            return reply
        await store.record_rejection(conn, sender_hash, msg.origin)
        telemetry.count("rejected", "not_member")
        return None
    business, vault = await store.open_business(conn, ports.keyring, member.business_id)
    if msg.origin == "wazenda":
        await store.touch_member(conn, sender_hash)  # opens Meta's 24 h window (T-18b)
    if _is_command(command):
        session = enrollment.Session(conn, business, vault, member, ports.send_email, msg.origin)
        return await enrollment.run(session, command)
    if msg.kind == "text" and (tried := misused_command(msg.text)):
        return t(business.language, f"usage.{tried}")  # a command typed wrong is never a case
    if msg.kind == "text" and not any(c.isalnum() for c in msg.text or ""):
        return t(business.language, "case.empty")  # specs/12 A1: no case, no model
    case = normalize(msg, business)
    if case is None:
        telemetry.count("rejected", "unsupported")
        return t(business.language, "case.unsupported")
    ref = await store.save_case(conn, vault, case)
    if ref is None:
        telemetry.count("rejected", "duplicate")
        return t(business.language, "case.duplicate")
    await ports.analyze(case.model_copy(update={"ref": ref}))
    return None


def _is_command(command: Command | None) -> TypeGuard[Command]:
    """A member's ``hola <text>`` is a forwarded message ("Hola mami..."), not a command."""
    return command is not None and not (command.name == "enroll" and command.args)


def normalize(msg: IncomingMessage, business: Business) -> Case | None:
    """Turn a member's message into a Case.

    Args:
        msg: Incoming message from an enrolled member.
        business: Their business; gives the country.

    Returns:
        The case, or ``None`` if the type is not supported.
    """
    kind = _kind(msg)
    if kind is None:
        return None
    text, email = _email(msg) if kind == "email" else (msg.text, None)
    text = clean_text(text) if text is not None else None
    if kind == "text" and (chat := parse_chat(text or "")):
        # specs/15: pasted or exported chat; the pack says who asks for the money
        pack = country_pack(business.country)
        asks = partial(asks_payment, pack=pack) if pack else never
        keep = partial(is_trade_word, pack=pack) if pack else never
        kind, text = "conversation", render_chat(chat, asks, keep)
    return Case(
        id=uuid4(),
        business_id=business.id,
        country=business.country,
        origin=msg.origin,
        sender=msg.sender,
        message_id=msg.message_id,
        kind=kind,
        text=text,
        urls=extract_urls(text or ""),
        attachments=msg.attachments,
        email=email,
        received_at=msg.received_at,
    )


def _kind(msg: IncomingMessage) -> CaseKind | None:
    """Case kind from the channel kind and MIME; ``None`` if unsupported."""
    if msg.kind == "text":
        text = (msg.text or "").strip()
        return "url" if len(text.split()) == 1 and extract_urls(text) else "text"
    if msg.kind in ("image", "audio", "email"):
        return msg.kind
    if msg.kind == "document" and {a.mime for a in msg.attachments} == {"application/pdf"}:
        return "pdf"
    return None


def _email(msg: IncomingMessage) -> tuple[str | None, EmailMeta]:
    """Body and sender headers; inline forwards have no headers."""
    if msg.eml_bytes is None:
        return msg.text, EmailMeta(with_headers=False)
    mail = BytesParser(policy=policy.default).parsebytes(msg.eml_bytes)
    body = mail.get_body(preferencelist=("plain", "html"))
    name, address = parseaddr(str(mail.get("From", "")))
    meta = EmailMeta(
        sender=address or None,
        display_name=name or None,
        reply_to=mail.get("Reply-To"),
        return_path=mail.get("Return-Path"),
        auth_results=mail.get("Authentication-Results"),
        with_headers=True,
    )
    return (body.get_content().strip() if body else None), meta
