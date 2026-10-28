"""Image pieces shared by SAM, the QR reader and the analyst's ``crop`` tool (domain, D-076).

A box comes in the frame SAM answered on (``w`` x ``h``); :func:`crop` scales it to the
real image, so callers never care whether the model resized it.
"""

import io
from collections.abc import Callable

from PIL import Image, ImageOps
from pydantic import BaseModel

from vigia.domain.rejection import Rejection

CROP_MARGIN = 0.15  # share of the box added on each side: SAM boxes hug the QR's quiet zone
CROP_MIN_SIDE = 400  # px; small crops are upscaled to this before decoding (D-076)


class Box(BaseModel, frozen=True):
    """A region SAM found, in pixels of the ``w`` x ``h`` frame it answered on.

    Attributes:
        x1: Left edge.
        y1: Top edge.
        x2: Right edge.
        y2: Bottom edge.
        w: Width of SAM's frame.
        h: Height of SAM's frame.
    """

    x1: int
    y1: int
    x2: int
    y2: int
    w: int
    h: int


class Segmentation(BaseModel, frozen=True):
    """What SAM found for one prompt.

    Attributes:
        boxes: One box per instance, possibly none.
        usd: Cost of the call.
    """

    boxes: tuple[Box, ...]
    usd: float


QR_PROMPT = "qr code"
"""What SAM is asked for to find QR codes (reading step and ``crop``)."""

Segmenter = Callable[[bytes, str], Segmentation | Rejection]
"""Port: finds the instances of a prompt (``qr code``, ``logo``…) in an image."""


def open_image(data: bytes) -> Image.Image:
    """Decode an image fully, so a broken file fails here and not later.

    Args:
        data: Image bytes.

    Returns:
        The decoded image.

    Raises:
        ValueError: If the bytes are not an image PIL can decode (or a decompression bomb).
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a decodable image: {type(exc).__name__}") from exc
    return image


def crop(image: bytes, box: Box) -> bytes:
    """The box (plus a margin) as a grayscale PNG, upscaled when small.

    Args:
        image: The original image.
        box: A SAM box, in SAM's frame.

    Returns:
        PNG bytes; empty when the image does not decode or the box falls outside it.
    """
    try:
        img = open_image(image)
    except ValueError:
        return b""
    sx, sy = img.width / max(box.w, 1), img.height / max(box.h, 1)
    mx, my = (box.x2 - box.x1) * CROP_MARGIN, (box.y2 - box.y1) * CROP_MARGIN
    left, top = max(0, int((box.x1 - mx) * sx)), max(0, int((box.y1 - my) * sy))
    right = min(img.width, int((box.x2 + mx) * sx))
    bottom = min(img.height, int((box.y2 + my) * sy))
    if right <= left or bottom <= top:
        return b""
    piece = ImageOps.grayscale(img.crop((left, top, right, bottom)))
    scale = max(1, CROP_MIN_SIDE // max(piece.size))
    piece = piece.resize((piece.width * scale, piece.height * scale), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    piece.save(buf, format="PNG")
    return buf.getvalue()
