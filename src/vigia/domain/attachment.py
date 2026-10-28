"""Attachment checks that decode nothing: size cap, image and PDF magic, canonical WAV (domain)."""

import io
import wave
from collections.abc import AsyncIterable

from pydantic import BaseModel

from vigia.domain.image import check_canvas
from vigia.domain.rejection import Rejection

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
"""Largest attachment downloaded (specs/12 A6); bigger ones are refused mid-stream."""

CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE = 1, 2, 16000
"""The only audio Voice gets for now: mono, 16-bit PCM, 16 kHz WAV (specs/03, D-070)."""

_IMAGE_MIME = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


def image_mime(data: bytes) -> str | None:
    """MIME type of an image from its magic bytes, the same ones :func:`check_image` accepts.

    Args:
        data: File bytes.

    Returns:
        ``image/jpeg``, ``image/png``, ``image/gif`` or ``image/webp``; ``None`` otherwise.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((mime for magic, mime in _IMAGE_MIME.items() if data.startswith(magic)), None)


def check_image(data: bytes) -> Rejection | None:
    """Refuse an image by size, magic bytes and header, before anything decodes it.

    Over 10 MB or a header declaring more than 25 Mpx is ``TOO_LARGE`` (D-077); not a
    JPEG, PNG, GIF or WebP, or a header PIL cannot read, is ``INVALID_ATTACHMENT``.

    ponytail: the header is checked, the pixels are not; a truncated image with a valid
    header passes here and decodes to nothing later (no QR, no text).

    Args:
        data: Downloaded file.

    Returns:
        ``None`` if it looks like a sane image, else ``TOO_LARGE`` or ``INVALID_ATTACHMENT``.
    """
    if len(data) > MAX_ATTACHMENT_BYTES:
        return Rejection(error_code="TOO_LARGE", message=f"over {MAX_ATTACHMENT_BYTES} bytes")
    if not image_mime(data):
        detail = "empty file" if not data else "not a JPEG, PNG, GIF or WebP"
        return Rejection(error_code="INVALID_ATTACHMENT", message=detail)
    return check_canvas(data)


def check_pdf(data: bytes, max_bytes: int) -> Rejection | None:
    """Refuse PDFs over ``max_bytes`` and files that do not start with ``%PDF-``.

    ponytail: magic and size only; the page count inside the cap is unbounded and the
    reader's output is capped instead (D-071).

    Args:
        data: Downloaded file.
        max_bytes: Largest PDF read (``Settings.pdf_max_bytes``).

    Returns:
        ``None`` if it looks like a PDF, else a ``TOO_LARGE`` or ``INVALID_ATTACHMENT``
        rejection.
    """
    if len(data) > max_bytes:
        return Rejection(error_code="TOO_LARGE", message=f"pdf over {max_bytes} bytes")
    if not data.startswith(b"%PDF-"):
        return Rejection(error_code="INVALID_ATTACHMENT", message="not a PDF file")
    return None


def check_wav(data: bytes, max_seconds: int) -> float | Rejection:
    """Accept only canonical WAV no longer than ``max_seconds``.

    ponytail: no conversion; ogg/opus from WhatsApp is refused until ffmpeg lands (T-10/T-19).

    Args:
        data: Downloaded file.
        max_seconds: Longest audio accepted (``Settings.voice_max_seconds``).

    Returns:
        The duration in seconds, or an ``UNSUPPORTED_AUDIO``, ``AUDIO_TOO_LONG`` or
        ``TOO_LARGE`` rejection.
    """
    if len(data) > MAX_ATTACHMENT_BYTES:
        return Rejection(error_code="TOO_LARGE", message=f"audio over {MAX_ATTACHMENT_BYTES} bytes")
    try:
        with wave.open(io.BytesIO(data)) as audio:
            shape = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
            frames: int = audio.getnframes()
    except (wave.Error, EOFError):
        return Rejection(error_code="UNSUPPORTED_AUDIO", message="not a PCM WAV file")
    if shape != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE) or not frames:
        return Rejection(error_code="UNSUPPORTED_AUDIO", message=f"wav {shape}, {frames} frames")
    seconds = frames / SAMPLE_RATE
    if seconds > max_seconds:
        return Rejection(error_code="AUDIO_TOO_LONG", message=f"{seconds:.0f} s > {max_seconds} s")
    return seconds


async def read_capped(
    chunks: AsyncIterable[bytes], limit: int = MAX_ATTACHMENT_BYTES
) -> bytes | Rejection:
    """Read a download chunk by chunk and stop as soon as it passes ``limit``.

    Args:
        chunks: Body of the download, e.g. ``response.aiter_bytes()``.
        limit: Maximum size in bytes.

    Returns:
        The whole file, or a ``TOO_LARGE`` rejection without reading the rest.
    """
    data = bytearray()
    async for chunk in chunks:
        data += chunk
        if len(data) > limit:
            return Rejection(error_code="TOO_LARGE", message=f"attachment over {limit} bytes")
    return bytes(data)


class Attachment(BaseModel):
    """A file that came with the case; downloaded and stored by later stages.

    Attributes:
        ref: Channel reference of the file.
        mime: Declared MIME type.
    """

    ref: str
    mime: str | None = None
