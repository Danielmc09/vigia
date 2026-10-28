"""Voice note or PDF to text, in code and before the signals run (application, D-070, D-071).

Not an analyst tool: the deterministic signals (rule 5) need the words, so the text becomes
``Case.text`` and the case keeps its ``kind`` (``audio`` or ``pdf``).
"""

from collections.abc import Mapping

from vigia.domain.attachment import check_pdf, check_wav
from vigia.domain.rejection import Rejection, RejectionCode
from vigia.domain.voice import Transcriber, Transcript
from vigia.i18n import t

REPLY_KEY: dict[RejectionCode, str] = {
    "UNSUPPORTED_AUDIO": "audio.unsupported",
    "AUDIO_TOO_LONG": "audio.too_long",
    "TOO_LARGE": "audio.too_large",
    "MODEL_UNAVAILABLE": "audio.unavailable",
}
"""i18n key of the reply for each way a voice note can fail to become text."""

PDF_REPLY_KEY: dict[RejectionCode, str] = {
    "INVALID_ATTACHMENT": "pdf.invalid",
    "TOO_LARGE": "pdf.too_large",
    "MODEL_UNAVAILABLE": "pdf.unavailable",
}
"""i18n key of the reply for each way a PDF can fail to become text."""


def transcribe(
    wav: bytes, language: str, transcriber: Transcriber, max_seconds: int
) -> Transcript | Rejection:
    """Check the audio, then spend on Voice only if it passes.

    Args:
        wav: Downloaded voice note.
        language: Language bias for Voice (the country pack's language).
        transcriber: Voice, or a fake in tests.
        max_seconds: Longest audio accepted (``Settings.voice_max_seconds``).

    Returns:
        The transcript, or why it could not be made.
    """
    checked = check_wav(wav, max_seconds)
    if isinstance(checked, Rejection):
        return checked
    return transcriber(wav, language)


def read_pdf(
    pdf: bytes, language: str, reader: Transcriber, max_bytes: int
) -> Transcript | Rejection:
    """Check the PDF, then spend on Spark only if it passes.

    Args:
        pdf: Downloaded document.
        language: Language hint (the country pack's language).
        reader: ``SparkClient.read_pdf``, or a fake in tests.
        max_bytes: Largest PDF accepted (``Settings.pdf_max_bytes``).

    Returns:
        The text read, or why it could not be read.
    """
    checked = check_pdf(pdf, max_bytes)
    if checked is not None:
        return checked
    return reader(pdf, language)


def reply(
    rejection: Rejection, language: str, keys: Mapping[RejectionCode, str] = REPLY_KEY
) -> str:
    """What the member reads when their voice note or PDF could not be read.

    Args:
        rejection: Output of :func:`transcribe` or :func:`read_pdf`.
        language: Language of the business.
        keys: :data:`REPLY_KEY` for audio, :data:`PDF_REPLY_KEY` for PDFs.

    Returns:
        The i18n message for that rejection; unknown codes read as "unavailable".
    """
    return t(language, keys.get(rejection.error_code, keys["MODEL_UNAVAILABLE"]))
