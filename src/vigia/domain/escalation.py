"""An irreversible action pending the owner's confirmation (specs/05) (domain)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

EscalationAction = Literal["report", "block_sender", "delete_case", "transfer_ownership"]
Status = Literal["pending", "confirmed", "cancelled", "expired"]

EXPIRY = timedelta(hours=24)
"""specs/05: an escalation not confirmed within this window can no longer run."""


@dataclass(frozen=True)
class Escalation:
    """One irreversible action, waiting for ``confirmar {id}`` from the owner.

    Attributes:
        id: Short id quoted in the reply to the owner.
        business_id: Business it belongs to.
        action: What it would do.
        target: Where it would act (an email, a phone number, a case id...).
        created_at: When it was raised; it expires :data:`EXPIRY` later.
        status: Its current state.
    """

    id: str
    business_id: UUID
    action: EscalationAction
    target: str
    created_at: datetime
    status: Status = "pending"

    def is_expired(self, now: datetime) -> bool:
        """Whether ``now`` is past this escalation's expiry.

        Args:
            now: Time to check against.

        Returns:
            True once :data:`EXPIRY` has passed since ``created_at``.
        """
        return now >= self.created_at + EXPIRY
