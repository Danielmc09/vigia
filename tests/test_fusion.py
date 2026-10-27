"""Code-only fusion of specs/05 rules 1-4: signals decide without the analyst."""

import pytest

from vigia.domain.signal import Signal
from vigia.verdict.fusion import fuse


def _sig(kind: str, weight: str) -> Signal:
    return Signal(type=kind, weight=weight, source="patterns")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("signals", "label", "confidence"),
    [
        ([_sig("brand_off_domain", "hard")], "scam", 0.9),
        ([_sig("urgency", "medium"), _sig("prize", "medium")], "scam", 0.8),
        ([_sig("urgency", "medium")], "doubtful", 0.5),
        # the same medium twice (two .xyz links) is still one piece of evidence
        ([_sig("suspicious_tld", "medium"), _sig("suspicious_tld", "medium")], "doubtful", 0.5),
        ([_sig("official_domain", "light"), _sig("personal_payment", "light")], "doubtful", 0.5),
        ([], "doubtful", 0.5),
    ],
)
def test_fusion_without_analyst(signals: list[Signal], label: str, confidence: float) -> None:
    assert fuse(signals) == (label, confidence)
