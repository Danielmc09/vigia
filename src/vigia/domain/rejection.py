"""The Rejection: a refusal returned as data, never raised to the user (domain, specs/09)."""

from typing import Literal

from pydantic import BaseModel

RejectionCode = Literal[
    "INVALID_ATTACHMENT",
    "TOO_LARGE",
    # Audio (T-11): only canonical WAV, up to Settings.voice_max_seconds.
    "UNSUPPORTED_AUDIO",
    "AUDIO_TOO_LONG",
    # Policy (specs/05): why an action or a confirmar/cancelar did not go through.
    "UNKNOWN_ACTION",
    "NOT_CONFIRMED",
    "LOCAL_CHANNEL",
    "EXPIRED",
    "NOT_OWNER",
    "NOT_PENDING",
    # Analyst tools and model (specs/03): the model gets these back as data.
    "POLICY_ONLY",
    "UNKNOWN_TOOL",
    "BAD_ARGUMENTS",
    "LIMIT_REACHED",
    "MODEL_UNAVAILABLE",
    "CONTENT_FILTER",
    # Channel (T-10): the media could not be downloaded from Wazenda.
    "CHANNEL_UNAVAILABLE",
    # T-10e: a webhook attachment without media_url had no single match in the listing.
    "MEDIA_MATCH_FAILED",
]


class Rejection(BaseModel, frozen=True):
    """Why a step refused its input; the caller turns it into a verdict or a reply.

    Attributes:
        error_code: Machine-readable reason; doubles as the i18n key root.
        message: Short English detail for logs (never shown to the user).
        retryable: True when the same input may work later.
    """

    error_code: RejectionCode
    message: str
    retryable: bool = False
