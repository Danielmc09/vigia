"""The Case: one normalized message to analyze, and the raw message it comes from (domain)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Origin = Literal["wazenda", "email", "local", "eval"]
CaseKind = Literal["text", "image", "audio", "pdf", "url", "email"]
ChannelKind = Literal["text", "image", "audio", "document", "email", "unknown"]


class IncomingMessage(BaseModel):
    """What a channel adapter hands over, before whitelist and normalization (specs/01).

    Attributes:
        message_id: Channel id of the message; used for dedupe.
        origin: Channel that received it.
        sender: E.164 phone number or email address of the forwarder.
        kind: Channel-level type; ``document`` is refined by ``mime``.
        text: Body or caption, if any.
        attachment_ref: Channel reference to download the attachment.
        mime: MIME type of the attachment.
        eml_bytes: Raw ``.eml`` when an email was forwarded as attachment.
        received_at: When the channel received it.
    """

    message_id: str
    origin: Literal["wazenda", "email", "local"]
    sender: str
    kind: ChannelKind
    text: str | None = None
    attachment_ref: str | None = None
    mime: str | None = None
    eml_bytes: bytes | None = None
    received_at: datetime


class Attachment(BaseModel):
    """A file that came with the case; downloaded and stored by later stages.

    Attributes:
        ref: Channel reference of the file.
        mime: Declared MIME type.
    """

    ref: str
    mime: str | None = None


class EmailMeta(BaseModel):
    """Sender-side headers of a forwarded email; all ``None`` for inline forwards.

    Attributes:
        sender: Address in ``From``.
        display_name: Display name in ``From``.
        reply_to: ``Reply-To`` header.
        return_path: ``Return-Path`` header.
        auth_results: ``Authentication-Results`` header (SPF/DKIM/DMARC).
        with_headers: True when the original ``.eml`` was available.
    """

    sender: str | None = None
    display_name: str | None = None
    reply_to: str | None = None
    return_path: str | None = None
    auth_results: str | None = None
    with_headers: bool


class Case(BaseModel):
    """One message to analyze, normalized and tied to an enrolled business (specs/02).

    Attributes:
        id: Case id.
        business_id: Business of the member who forwarded it.
        country: ISO code of the business; selects the country pack.
        origin: Channel, or ``eval`` for the eval set.
        sender: Forwarder (phone or email), if any.
        message_id: Channel message id, if any.
        kind: Normalized type.
        text: Body, caption, email body or transcription.
        urls: Cleaned URLs found in ``text``.
        attachments: Files that came with it.
        email: Email headers when ``kind == "email"``.
        context: Extra context from the channel or the eval.
        received_at: When the channel received it.
    """

    id: UUID
    business_id: UUID
    country: str
    origin: Origin
    sender: str | None = None
    message_id: str | None = None
    kind: CaseKind
    text: str | None = None
    urls: list[str] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    email: EmailMeta | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime
