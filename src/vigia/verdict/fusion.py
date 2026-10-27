"""Fusion of specs/05: the code decides the label from the signals (rules).

Only the analyst-free rules 1-4 live here today; the analyst joins in T-07.
"""

from collections.abc import Sequence
from typing import cast, get_args

from vigia.domain.signal import Signal
from vigia.domain.verdict import Label

SCAM, LEGIT, DOUBTFUL = cast(tuple[Label, Label, Label], get_args(Label))
HARD_CONFIDENCE = 0.9  # specs/05 rule 1
MEDIUMS_FOR_SCAM = 2  # specs/05 rule 2; distinct signal types (D-034)
MEDIUMS_CONFIDENCE = 0.8
DOUBTFUL_CONFIDENCE = 0.5


def fuse(signals: Sequence[Signal]) -> tuple[Label, float]:
    """Label and confidence from deterministic signals alone.

    Args:
        signals: Signals of the case.

    Returns:
        ``scam`` with a hard signal or two distinct mediums; ``doubtful`` otherwise
        (without the analyst nothing is ever ``legit``).
    """
    if any(s.weight == "hard" for s in signals):
        return SCAM, HARD_CONFIDENCE
    if len({s.type for s in signals if s.weight == "medium"}) >= MEDIUMS_FOR_SCAM:
        return SCAM, MEDIUMS_CONFIDENCE
    return DOUBTFUL, DOUBTFUL_CONFIDENCE
