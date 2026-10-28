"""One case through the rules and, when they do not decide, the analyst (application).

The single pipeline of specs/09 for ``/caso``, ``measure.py`` and the adversarial runner:
signals → (analyst) → fusion. The gate runs before, per chat, because it needs history.
"""

from collections.abc import Callable, Sequence

from vigia.analyst import loop
from vigia.domain.analyst import AnalystResult, Evidence
from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.gate import GateResult
from vigia.domain.member import Supplier
from vigia.domain.verdict import Verdict
from vigia.model.spark import SparkClient
from vigia.services import analyst_tools
from vigia.signals.detector import detect
from vigia.verdict.build import build_verdict
from vigia.verdict.fusion import decided_by_rules, soften_citations

Analyst = Callable[[Case, Evidence, CountryPack, GateResult], AnalystResult | None]
"""The analyst's read of a case, given what the rules and the gate already found."""


def spark_analyst(spark: SparkClient, suppliers: Sequence[Supplier] = ()) -> Analyst:
    """The real analyst: the tool loop over Spark, web search only when triage asks.

    Args:
        spark: Spark client (its running ``usd`` bills the cases).
        suppliers: Address book offered to ``check_address_book``.

    Returns:
        An :data:`Analyst`.
    """

    def analyst(
        case: Case, evidence: Evidence, pack: CountryPack, gate: GateResult
    ) -> AnalystResult | None:
        search = spark if gate.triage == "investigate" else None
        tools = analyst_tools.toolbox(pack, suppliers, search)
        return loop.investigate(case, evidence, tools, spark.complete)

    return analyst


def classify(
    case: Case, pack: CountryPack, gate: GateResult, analyst: Analyst | None
) -> Verdict:
    """Signals, then the analyst only if the rules did not already decide, then fusion.

    Args:
        case: The normalized case.
        pack: Country pack of the business, campaigns attached.
        gate: What the gate (Jev and its rules floor) said about the chat.
        analyst: The analyst; ``None`` means no model (degraded verdict).

    Returns:
        The verdict of specs/05.
    """
    signals = detect(case, pack)
    evidence = Evidence(signals=signals, manipulation_detected=gate.manipulation_detected)
    if analyst is not None and not decided_by_rules(soften_citations(signals, case, pack)):
        evidence = evidence.model_copy(update={"analyst": analyst(case, evidence, pack, gate)})
    return build_verdict(case, evidence, pack)
