"""Wazenda adapter (specs/01): signature, payload, media download, ogg to WAV, sending."""

import asyncio
import hashlib
import hmac
import io
import json
import logging
import random
import re
import time
import wave
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import av
import httpx
from pydantic import BaseModel, Field, ValidationError

from vigia import telemetry
from vigia.domain.attachment import (
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    Attachment,
    read_capped,
)
from vigia.domain.case import IncomingMessage
from vigia.domain.member import normalize_phone
from vigia.domain.rejection import Rejection

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 700  # specs/01: WhatsApp-friendly cap of every outgoing text
RETRIES = 3  # specs/01: 429 is retried 3 times, 1/2/4 s plus jitter
PER_MINUTE = 55  # specs/01: own cap below Wazenda's 60 requests per minute per line
_SIGNATURE = re.compile(r"sha256=([0-9a-f]{64})")
_PLACEHOLDER = re.compile(r"\[(audio|image|video|document|sticker)\]")
MATCH_WINDOW = timedelta(seconds=120)
"""Outer sanity bound: how far a listed attachment may be from the webhook's timestamp."""
AMBIGUOUS_GAP = timedelta(seconds=5)
"""Two best candidates closer than this to each other are not told apart (D-103)."""


class _Payload(BaseModel):
    """The fields of ``message.received`` VIGIA reads; anything else is ignored."""

    event: str
    sender: str = Field(alias="from")
    message_id: str = Field(min_length=1)
    timestamp: datetime
    body: str | None = None
    media_url: str | None = None
    media_type: str | None = None


class _Listed(BaseModel):
    """One item of ``GET /messages`` (verified 31 oct); invalid items are skipped."""

    id: UUID
    direction: Literal["inbound", "outbound"]
    contact_number: str
    media_type: str | None = None
    media_url: str | None = None
    created_at: datetime


def signed_payload(secret: bytes | None, body: bytes, header: str | None) -> dict[str, Any] | None:
    """The webhook JSON, only if ``X-Wazenda-Signature`` signs these exact bytes.

    Args:
        secret: ``WAZENDA_WEBHOOK_SECRET``; ``None`` refuses everything (fail closed).
        body: Raw request body, before any parsing.
        header: ``X-Wazenda-Signature`` (``sha256=<hex>``).

    Returns:
        The decoded object, or ``None`` (the endpoint answers 401) when the secret is
        missing, the header is malformed or wrong, or the body is not a JSON object.
    """
    found = _SIGNATURE.fullmatch(header or "")
    if secret is None or found is None:
        return None
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, found.group(1)):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def to_message(raw: dict[str, Any]) -> IncomingMessage | None:
    """A ``message.received`` event as the channel-neutral incoming message.

    Media kind is left ``unknown``: the downloaded bytes decide it, never ``media_type``.
    A media webhook may carry only a placeholder body (``[audio]``) and no ``media_url``:
    its attachment then has an empty ``ref``, meaning "find it with ``latest_media``".

    Args:
        raw: Verified webhook JSON.

    Returns:
        The message, or ``None`` for other events, missing fields, or no body and no media.
    """
    try:
        payload = _Payload.model_validate(raw)
    except ValidationError:
        return None
    placeholder = _PLACEHOLDER.fullmatch((payload.body or "").strip()) is not None
    media = bool(payload.media_url or payload.media_type) or placeholder
    if payload.event != "message.received" or not (payload.body or media):
        return None
    received = payload.timestamp
    return IncomingMessage(
        message_id=payload.message_id,
        origin="wazenda",
        sender=normalize_phone(payload.sender),
        kind="unknown" if media else "text",
        text=None if placeholder else payload.body,
        attachments=[Attachment(ref=payload.media_url or "")] if media else [],
        received_at=_aware(received),
    )


def _aware(moment: datetime) -> datetime:
    """Read a naive provider timestamp as UTC.

    Args:
        moment: Timestamp parsed from a Wazenda payload or listing.

    Returns:
        The same instant with a timezone.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


HTTP_TIMEOUT_S = 30
"""Seconds for one call to Wazenda (send or media download)."""


def _unavailable(detail: str) -> Rejection:
    return Rejection(error_code="CHANNEL_UNAVAILABLE", message=detail, retryable=True)


@dataclass
class Wazenda:
    """Outbound calls to Wazenda for one line.

    Attributes:
        base_url: ``WAZENDA_BASE_URL``; its exact host is the only one media comes from.
        api_key: ``X-API-Key``.
        line_id: Line VIGIA sends from.
        http: Shared client (a ``MockTransport`` in tests).
        sleep: Waits between retries and under the rate cap (instant in tests).
        clock: Monotonic seconds for the rate cap.
        jitter: Extra seconds added to each backoff.
        per_minute: Own cap of requests per minute on the line.
    """

    base_url: str
    api_key: str
    line_id: str
    http: httpx.AsyncClient
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    jitter: Callable[[], float] = random.random
    per_minute: int = PER_MINUTE
    _sent: deque[float] = field(default_factory=deque, init=False, repr=False)

    def _trusted(self, url: str) -> bool:
        """Whether a media URL is https on exactly the base host, port, and no userinfo."""
        base = urlsplit(self.base_url)
        try:
            parts = urlsplit(url)
            port = parts.port or 443
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and parts.hostname in {base.hostname, f"www.{base.hostname}"}
            and parts.username is None
            and parts.password is None
            and port == (base.port or 443)
        )

    async def download(self, url: str) -> bytes | Rejection:
        """Download one media file, streaming, with the 10 MB cap on bytes received.

        Args:
            url: ``media_url`` of the webhook.

        Returns:
            The bytes; ``INVALID_ATTACHMENT`` for an untrusted URL (nothing is requested),
            ``TOO_LARGE`` past the cap, ``CHANNEL_UNAVAILABLE`` on any non-200 or network
            error. Never raises.
        """
        if not self._trusted(url):
            return Rejection(error_code="INVALID_ATTACHMENT", message="media url not on wazenda")
        headers = {"X-API-Key": self.api_key}
        try:
            async with self.http.stream("GET", url, headers=headers) as response:
                if response.status_code != httpx.codes.OK:
                    return _unavailable(f"media status {response.status_code}")
                return await read_capped(response.aiter_bytes())
        except httpx.HTTPError as exc:
            return _unavailable(type(exc).__name__)

    async def latest_media(self, sender: str, at: datetime) -> bytes | Rejection:
        """Find the attachment of a webhook without ``media_url`` and download it.

        The webhook carries a ``wamid``; the media endpoint wants Wazenda's own uuid, so
        the sender's last messages are listed and the inbound attachment closest to ``at``
        (within ``MATCH_WINDOW``) is taken, unless the runner-up is within
        ``AMBIGUOUS_GAP`` of it: then nothing is guessed (D-103). A trusted listed
        ``media_url`` is fetched directly; otherwise Wazenda's uuid.

        Args:
            sender: Normalized phone (``+57...``) of the webhook.
            at: The webhook's timestamp.

        Returns:
            The bytes (same checks as ``download``); ``MEDIA_MATCH_FAILED`` for no candidate
            or two too close to tell, ``CHANNEL_UNAVAILABLE`` if the listing fails. Never raises.
        """
        contact = sender.lstrip("+")
        params = {"line_id": self.line_id, "contact": contact, "limit": 5}
        try:
            response = await self.http.get(
                f"{self.base_url}/messages", params=params, headers={"X-API-Key": self.api_key}
            )
        except httpx.HTTPError as exc:
            return _unavailable(type(exc).__name__)
        if response.status_code != httpx.codes.OK:
            return _unavailable(f"listing status {response.status_code}")
        try:
            items = response.json()
        except ValueError:
            items = None
        found: list[tuple[timedelta, _Listed]] = []
        for raw in items if isinstance(items, list) else []:
            try:
                item = _Listed.model_validate(raw)
            except ValidationError:
                continue
            distance = abs(_aware(item.created_at) - at)
            if (
                item.direction == "inbound"
                and item.media_type
                and item.contact_number == contact
                and distance <= MATCH_WINDOW
            ):
                found.append((distance, item))
        found.sort(key=lambda pair: pair[0])
        if not found or (len(found) > 1 and found[1][0] - found[0][0] < AMBIGUOUS_GAP):
            return Rejection(error_code="MEDIA_MATCH_FAILED", message=f"{len(found)} candidates")
        item = found[0][1]
        if item.media_url and self._trusted(item.media_url):
            return await self.download(item.media_url)
        return await self.download(f"{self.base_url}/messages/{item.id}/media")

    async def _under_cap(self) -> None:
        """Wait until one more request fits in the last minute.

        ponytail: per process and approximate under concurrency; Redis when there are
        replicas (T-16).
        """
        if len(self._sent) >= self.per_minute:
            wait = self._sent[0] + 60 - self.clock()
            if wait > 0:
                await self.sleep(wait)
            self._sent.popleft()
        self._sent.append(self.clock())

    async def send(self, number: str, text: str) -> bool:
        """Send a WhatsApp text, retrying 429 with backoff.

        Args:
            number: Recipient phone.
            text: Code-built message (rule 14); cut to 700 characters.

        Returns:
            ``True`` once Wazenda accepted it; ``False`` after the retries or on any error.
        """
        body = {"line_id": self.line_id, "number": number, "message": text[:MAX_MESSAGE_CHARS]}
        return await self._post("/messages", body, number)

    async def send_template(
        self, number: str, template: str, language: str, params: list[str]
    ) -> bool:
        """Send an approved Meta template, the only way past the 24 h window (D-097).

        Args:
            number: Recipient phone.
            template: Approved template name.
            language: Template language code (``es``, ``en_US``).
            params: Body parameters in order, code-built (rule 14).

        Returns:
            ``True`` once Wazenda accepted it; ``False`` after the retries or on any error.
        """
        body = {
            "line_id": self.line_id,
            "number": number,
            "template_name": template,
            "language": language,
            "body_params": params,
        }
        return await self._post("/templates", body, number)

    async def _post(self, path: str, body: dict[str, object], number: str) -> bool:
        """POST to Wazenda, retrying 429 with backoff; log failures with the phone masked."""
        headers = {"X-API-Key": self.api_key}
        for attempt in range(RETRIES + 1):
            await self._under_cap()
            try:
                response = await self.http.post(
                    f"{self.base_url}{path}", json=body, headers=headers
                )
            except httpx.HTTPError as exc:
                log.warning(
                    "wazenda_send_failed to=%s error=%s",
                    telemetry.mask_phone(number),
                    type(exc).__name__,
                )
                return False
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS or attempt == RETRIES:
                break
            await self.sleep(2**attempt + self.jitter())
        if response.is_success:
            return True
        log.warning(
            "wazenda_send_failed to=%s status=%s",
            telemetry.mask_phone(number),
            response.status_code,
        )
        return False


def ogg_to_wav(data: bytes, max_seconds: int) -> bytes | Rejection:
    """Decode a WhatsApp voice note (ogg/opus) to the canonical WAV Voice gets (D-079).

    Decoding stops as soon as it passes ``max_seconds``, so a long note costs no more.

    Args:
        data: Downloaded ogg file.
        max_seconds: Longest audio accepted (``Settings.voice_max_seconds``).

    Returns:
        Mono 16-bit 16 kHz WAV bytes, or ``UNSUPPORTED_AUDIO`` / ``AUDIO_TOO_LONG``.
    """
    pcm, limit = bytearray(), max_seconds * SAMPLE_RATE * SAMPLE_WIDTH
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    pcm += bytes(out.planes[0])[: out.samples * SAMPLE_WIDTH]
                if len(pcm) > limit:
                    return Rejection(error_code="AUDIO_TOO_LONG", message=f"over {max_seconds} s")
    except (av.FFmpegError, ValueError, IndexError) as exc:
        return Rejection(error_code="UNSUPPORTED_AUDIO", message=type(exc).__name__)
    if not pcm:
        return Rejection(error_code="UNSUPPORTED_AUDIO", message="no audio frames")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out_wav:
        out_wav.setnchannels(CHANNELS)
        out_wav.setsampwidth(SAMPLE_WIDTH)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(bytes(pcm))
    return buf.getvalue()
