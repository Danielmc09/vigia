"""Voice note, PDF or image to text, in code and before the signals run (application).

Not an analyst tool: the deterministic signals (rule 5) need the words, so the text becomes
``Case.text`` and the case keeps its ``kind`` (``audio``, ``pdf`` or ``image``). An image's QR
links are decoded here too and travel apart, as ``Case.qr_urls`` (D-070, D-071, D-076).
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from vigia.domain.attachment import check_image, check_pdf, check_wav
from vigia.domain.image import QR_PROMPT, Segmenter
from vigia.domain.reading import Transcriber, Transcript
from vigia.domain.rejection import Rejection, RejectionCode
from vigia.i18n import t
from vigia.signals.qr import qr_urls

if TYPE_CHECKING:
    from vigia.config import Settings

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

IMAGE_REPLY_KEY: dict[RejectionCode, str] = {
    "INVALID_ATTACHMENT": "image.invalid",
    "TOO_LARGE": "image.too_large",
    "MODEL_UNAVAILABLE": "image.unavailable",
}
"""i18n key of the reply for each way an image can fail to become text."""

REPLY_KEYS: dict[str, Mapping[RejectionCode, str]] = {
    "audio": REPLY_KEY,
    "pdf": PDF_REPLY_KEY,
    "image": IMAGE_REPLY_KEY,
}
"""The reply keys of each reader kind, for :func:`reply`."""


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


def read_image(image: bytes, language: str, reader: Transcriber) -> Transcript | Rejection:
    """Check the image, then spend on the reader only if it passes.

    Args:
        image: Downloaded screenshot or photo.
        language: Language hint (the country pack's language).
        reader: :func:`image_reader`, or a fake in tests.

    Returns:
        The text and QR links read, or why the image could not be read.
    """
    checked = check_image(image)
    if checked is not None:
        return checked
    return reader(image, language)


def find_qr(image: bytes, segment: Segmenter | None) -> tuple[tuple[str, ...], float]:
    """QR links of an image: zxing on the whole image, then on SAM's crops if it found none.

    Args:
        image: A checked image.
        segment: SAM, or ``None`` to only try the whole image.

    Returns:
        The links and what SAM cost (0 when it was not needed or failed).
    """
    found = qr_urls(image)
    if found or segment is None:
        return tuple(found), 0.0
    boxes = segment(image, QR_PROMPT)
    if isinstance(boxes, Rejection):
        return (), 0.0
    return tuple(qr_urls(image, boxes.boxes)), boxes.usd


def image_reader(read: Transcriber, segment: Segmenter | None) -> Transcriber:
    """The :data:`Transcriber` of images: QR links in code, visible text by the model.

    Args:
        read: ``SparkClient.read_image``, or a fake in tests.
        segment: SAM for small QR codes, or ``None``.

    Returns:
        A reader whose transcript carries ``qr_urls`` and the cost of both models.
    """

    def reader(image: bytes, language: str) -> Transcript | Rejection:
        links, sam_usd = find_qr(image, segment)
        text = read(image, language)
        if isinstance(text, Rejection):
            return text
        return text.model_copy(update={"qr_urls": links, "usd": text.usd + sam_usd})

    return reader


def checked(kind: str, reader: Transcriber, config: "Settings") -> Transcriber:
    """The reader of ``kind`` behind its free check: the model is paid only if it passes.

    Args:
        kind: ``audio``, ``pdf``, or anything else for an image.
        reader: Voice, Spark's PDF reader or :func:`image_reader`, or a fake in tests.
        config: Settings with the audio and PDF caps.

    Returns:
        A :data:`Transcriber` that runs :func:`transcribe`, :func:`read_pdf` or
        :func:`read_image` (blocking: it calls the model).
    """
    if kind == "audio":
        return lambda data, lang: transcribe(data, lang, reader, config.voice_max_seconds)
    if kind == "pdf":
        return lambda data, lang: read_pdf(data, lang, reader, config.pdf_max_bytes)
    return lambda data, lang: read_image(data, lang, reader)


def reply(
    rejection: Rejection, language: str, keys: Mapping[RejectionCode, str] = REPLY_KEY
) -> str:
    """What the member reads when their voice note, PDF or image could not be read.

    Args:
        rejection: Output of :func:`transcribe`, :func:`read_pdf` or :func:`read_image`.
        language: Language of the business.
        keys: :data:`REPLY_KEY` for audio, :data:`PDF_REPLY_KEY` for PDFs,
            :data:`IMAGE_REPLY_KEY` for images.

    Returns:
        The i18n message for that rejection; unknown codes read as "unavailable".
    """
    return t(language, keys.get(rejection.error_code, keys["MODEL_UNAVAILABLE"]))
