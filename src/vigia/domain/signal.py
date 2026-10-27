"""The Signal: one piece of deterministic evidence about a case (domain)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

# hard decides alone, two distinct medium types decide together, light only informs.
Weight = Literal["hard", "medium", "light"]
SignalSource = Literal["urls", "qr", "patterns", "email", "address_book", "country", "knowledge"]

WEIGHT_ORDER: tuple[Weight, ...] = ("hard", "medium", "light")
"""Weights from strongest to weakest; signals are reported in this order."""


class Signal(BaseModel, frozen=True):
    """Deterministic evidence (specs/04).

    ``type`` doubles as the i18n key root (``signal.<type>``) when a verdict explains it;
    signals carry no free text.

    Attributes:
        type: What fired, e.g. ``brand_off_domain``.
        weight: How much it counts in fusion.
        source: Which rule family produced it.
        evidence: Data behind it (url, domain, matched text, brand...).
    """

    type: str
    weight: Weight
    source: SignalSource
    evidence: dict[str, Any] = Field(default_factory=dict)
