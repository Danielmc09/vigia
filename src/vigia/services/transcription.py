"""Voice note to text, in code and before the signals run (application, D-070).

Not an analyst tool: the deterministic signals (rule 5) need the words, so the transcript
becomes ``Case.text`` and the case keeps ``kind="audio"``.
"""

from vigia.domain.attachment import check_wav
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


def reply(rejection: Rejection, language: str) -> str:
    """What the member reads when their voice note could not be transcribed.

    Args:
        rejection: Output of :func:`transcribe`.
        language: Language of the business.

    Returns:
        The i18n message for that rejection.
    """
    return t(language, REPLY_KEY.get(rejection.error_code, "audio.unavailable"))
