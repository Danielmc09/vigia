"""The Rejection: a refusal returned as data, never raised to the user (domain, specs/09)."""

from typing import Literal

from pydantic import BaseModel

RejectionCode = Literal["INVALID_ATTACHMENT", "TOO_LARGE"]


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
