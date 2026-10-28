"""Contract of reading an attachment into text: voice (T-11), PDF (T-13), image (T-12).

Shared by the Voice and Spark adapters (``model``), the transcription step (``services``) and
the eval runner, so it lives in the domain.
"""

from collections.abc import Callable

from pydantic import BaseModel

from vigia.domain.rejection import Rejection


class Transcript(BaseModel, frozen=True):
    """What Voice heard, or what Spark read from a PDF or an image (D-071, D-076).

    Attributes:
        text: The transcription; it becomes ``Case.text`` and feeds only the signals.
        audio_ms: Audio duration Voice billed; 0 for a PDF or an image.
        usd: Cost of the call (for an image, plus SAM's when it looked for the QR).
        qr_urls: Links decoded from the QR codes of an image; they become ``Case.qr_urls``.
    """

    text: str
    audio_ms: int = 0
    usd: float
    qr_urls: tuple[str, ...] = ()


Transcriber = Callable[[bytes, str], Transcript | Rejection]
"""Port: a file (canonical WAV, PDF or image) and a language hint (``es``/``en``) to text."""
