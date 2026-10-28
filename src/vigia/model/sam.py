"""SAM 3.1 over HTTP (adapter): the real :data:`~vigia.domain.image.Segmenter` (D-076).

Request and answer verified in T-02 (specs/03 "Verificado",
``tests/fixtures/meta/sam_segment.json``):
``POST /v1/responses`` with ``stream: true``; the boxes come as special tokens in the text of
the ``response.output_text.done`` event. Masks are ignored: a box is enough to crop.
Any failure is a :class:`~vigia.domain.rejection.Rejection`, never an exception.
"""

import base64
import json
import logging
import re

import httpx

from vigia.config import Settings
from vigia.domain.attachment import image_mime
from vigia.domain.image import Box, Segmentation
from vigia.domain.rejection import Rejection
from vigia.model.spark import BASE

MODEL = "sam-3.1"
PRICE_IMAGE = 0.0025  # USD per image, Standard tier (specs/03 "Verificado")
TIMEOUT_S = 10.0  # specs/12 C8; verified calls took 1.6-3.3 s
_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")
_DONE = "response.output_text.done"

log = logging.getLogger(__name__)


def parse_boxes(text: str) -> tuple[Box, ...]:
    """The boxes in SAM's answer text.

    Args:
        text: Text of the ``response.output_text.done`` event.

    Returns:
        One :class:`Box` per ``<|box;...|>`` token, in order; none if there is no token.
    """
    keys = ("x1", "y1", "x2", "y2", "w", "h")
    return tuple(
        Box(**dict(zip(keys, map(int, m.groups()), strict=True))) for m in _BOX.finditer(text)
    )


def _answer(stream: str) -> str:
    """Text of the final ``output_text.done`` event of an SSE body."""
    for line in stream.splitlines():
        if line.startswith("data: {"):
            event = json.loads(line[len("data: ") :])
            if event.get("type") == _DONE:
                return str(event["text"])
    raise ValueError("no output_text.done event")


class SamClient:
    """Asks SAM 3.1 for the boxes of a prompt; keeps the running cost.

    Attributes:
        usd: USD spent by this client so far.
    """

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        """Keep the key and the HTTP client.

        Args:
            api_key: Model API key, from settings only.
            client: HTTP client; tests pass one with ``httpx.MockTransport``.
        """
        self._key = api_key
        self._client = client or httpx.Client()
        self.usd = 0.0

    def __call__(self, image: bytes, prompt: str) -> Segmentation | Rejection:
        """Segment one image (already checked by :func:`~vigia.domain.attachment.check_image`).

        Args:
            image: Image bytes.
            prompt: What to find, e.g. ``qr code`` or ``logo``.

        Returns:
            The boxes billed per image, or a retryable ``MODEL_UNAVAILABLE``.
        """
        url = f"data:{image_mime(image)};base64," + base64.b64encode(image).decode()
        content = [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": url},
        ]
        body = {
            "model": MODEL,
            "input": [{"type": "message", "role": "user", "content": content}],
            "stream": True,
        }
        try:
            response = self._client.post(
                f"{BASE}/responses",
                headers={"Authorization": f"Bearer {self._key}"},
                json=body,
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            boxes = parse_boxes(_answer(response.text))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("sam unavailable: %s", type(exc).__name__)
            return Rejection(
                error_code="MODEL_UNAVAILABLE", message=type(exc).__name__, retryable=True
            )
        self.usd += PRICE_IMAGE
        return Segmentation(boxes=boxes, usd=PRICE_IMAGE)


def make_sam(config: Settings) -> SamClient | None:
    """The SAM client for this process, or ``None`` without ``MODEL_API_KEY``.

    Args:
        config: Process settings.

    Returns:
        A client, or ``None`` (small QR codes are then only read when zxing finds them whole).
    """
    if config.model_api_key is None:
        return None
    return SamClient(config.model_api_key.get_secret_value())
