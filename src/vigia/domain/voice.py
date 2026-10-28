"""Contract of transcription (T-11): the transcript and its port (domain).

Shared by the Voice adapter (``model``), the transcription step (``services``) and the eval
runner, so it lives in the domain.
"""

from collections.abc import Callable

from pydantic import BaseModel

from vigia.domain.rejection import Rejection


class Transcript(BaseModel, frozen=True):
    """What Voice heard.

    Attributes:
        text: The transcription; it becomes ``Case.text``.
        audio_ms: Audio duration Voice billed.
        usd: Cost of the call.
    """

    text: str
    audio_ms: int
    usd: float


Transcriber = Callable[[bytes, str], Transcript | Rejection]
"""Port: turns a canonical WAV and a language hint (``es``/``en``) into a transcript."""
