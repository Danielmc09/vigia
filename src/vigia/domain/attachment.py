"""Attachment checks that need no decoder: size cap while streaming, image magic bytes (domain)."""

from collections.abc import AsyncIterable

from pydantic import BaseModel

from vigia.domain.rejection import Rejection

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
"""Largest attachment downloaded (specs/12 A6); bigger ones are refused mid-stream."""

_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")


def check_image(data: bytes) -> Rejection | None:
    """Refuse empty files and files that do not start like a JPEG, PNG, GIF or WebP.

    ponytail: magic bytes only; a truncated image with a valid header passes until the
    image tools of T-12 decode it.

    Args:
        data: Downloaded file.

    Returns:
        ``None`` if it looks like an image, else an ``INVALID_ATTACHMENT`` rejection.
    """
    webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if data.startswith(_IMAGE_MAGIC) or webp:
        return None
    detail = "empty file" if not data else "not a JPEG, PNG, GIF or WebP"
    return Rejection(error_code="INVALID_ATTACHMENT", message=detail)


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
