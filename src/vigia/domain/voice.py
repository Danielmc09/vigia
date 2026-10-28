"""Contract of transcription (T-11) and PDF reading (T-13): the transcript and its port.

Shared by the Voice and Spark adapters (``model``), the transcription step (``services``) and
the eval runner, so it lives in the domain.
"""

from collections.abc import Callable

from pydantic import BaseModel

from vigia.domain.rejection import Rejection


class Transcript(BaseModel, frozen=True):
    """What Voice heard, or what Spark read from a PDF (D-071).

    Attributes:
        text: The transcription; it becomes ``Case.text`` and feeds only the signals.
        audio_ms: Audio duration Voice billed; 0 for a PDF.
        usd: Cost of the call.
    """

    text: str
    audio_ms: int = 0
    usd: float


Transcriber = Callable[[bytes, str], Transcript | Rejection]
"""Port: turns a file (canonical WAV or PDF) and a language hint (``es``/``en``) into text."""
