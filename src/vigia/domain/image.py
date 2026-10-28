"""Image pieces shared by SAM, the QR reader and the analyst's ``crop`` tool (domain, D-076).

A box comes in the frame SAM answered on (``w`` x ``h``); :func:`crop` scales it to the
real image, so callers never care whether the model resized it.
"""

import io
from collections.abc import Callable, Sequence

from PIL import Image, ImageOps
from pydantic import BaseModel

from vigia.domain.rejection import Rejection

CROP_MARGIN = 0.15  # share of the box added on each side: SAM boxes hug the QR's quiet zone
CROP_MIN_SIDE = 400  # px; small crops are upscaled to this before decoding (D-076)
MAX_IMAGE_PIXELS = 25_000_000  # D-077: a phone screenshot is ~3 Mpx; above this, refused
MAX_BOXES = 8  # D-077: SAM boxes cropped and decoded per image, largest first
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS  # safety net: PIL raises above twice it


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


def check_canvas(data: bytes) -> Rejection | None:
    """Refuse an image from its header alone: unreadable, or over ``MAX_IMAGE_PIXELS``.

    ``Image.open`` reads the header without decoding, so a small file declaring a huge
    canvas (incident adv-imagen-pixeles) costs nothing here.

    Args:
        data: Image bytes.

    Returns:
        ``None`` if the header is sane, else ``TOO_LARGE`` or ``INVALID_ATTACHMENT``.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        width, height = MAX_IMAGE_PIXELS, 2  # over the cap, whatever the warning filter says
    except OSError:
        return Rejection(error_code="INVALID_ATTACHMENT", message="unreadable image header")
    if width * height > MAX_IMAGE_PIXELS:  # explicit: warning filters are not thread-safe
        return Rejection(error_code="TOO_LARGE", message=f"over {MAX_IMAGE_PIXELS} px")
    return None


def open_image(data: bytes) -> Image.Image:
    """Decode an image fully, so a broken file fails here and not later.

    The header goes through :func:`check_canvas` first: nothing over the cap is decoded.

    Args:
        data: Image bytes.

    Returns:
        The decoded image.

    Raises:
        ValueError: If the header is refused or the bytes do not decode.
    """
    if refused := check_canvas(data):
        raise ValueError(refused.message)
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a decodable image: {type(exc).__name__}") from exc
    return image


def largest(boxes: Sequence[Box], limit: int = MAX_BOXES) -> list[Box]:
    """The ``limit`` boxes of largest area, largest first (incident adv-sam-cajas).

    Args:
        boxes: Boxes of one SAM answer (same frame, so areas compare).
        limit: How many to keep.

    Returns:
        At most ``limit`` boxes.
    """
    return sorted(boxes, key=lambda b: (b.x2 - b.x1) * (b.y2 - b.y1), reverse=True)[:limit]


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
