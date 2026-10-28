"""One case through the rules and, when they do not decide, the analyst (application).

The single pipeline of specs/09 for ``/caso``, ``measure.py`` and the adversarial runner:
signals → (analyst) → fusion. The gate runs before, per chat, because it needs history.
"""

from collections.abc import Callable, Sequence

from vigia.analyst import loop
from vigia.domain.analyst import AnalystResult, Evidence
from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.gate import GateResult, Judge
from vigia.domain.image import Segmenter
from vigia.domain.member import Supplier
from vigia.domain.verdict import Verdict
from vigia.jev.gate import screen
from vigia.model.spark import SparkClient
from vigia.services import analyst_tools
from vigia.signals.detector import detect
from vigia.verdict.build import build_verdict
from vigia.verdict.fusion import decided_by_rules, soften_citations

Analyst = Callable[[Case, Evidence, CountryPack, GateResult], AnalystResult | None]
"""The analyst's read of a case, given what the rules and the gate already found."""

Loader = Callable[[str], bytes]
"""Port: the bytes of an attachment, by its ``ref`` (a path in evals, the channel's media later)."""


def spark_analyst(
    spark: SparkClient,
    suppliers: Sequence[Supplier] = (),
    segment: Segmenter | None = None,
    load: Loader | None = None,
) -> Analyst:
    """The real analyst: the tool loop over Spark, web search only when triage asks.

    Args:
        spark: Spark client (its running ``usd`` bills the cases).
        suppliers: Address book offered to ``check_address_book``.
        segment: SAM; with ``load`` it offers ``crop`` on image cases (D-076).
        load: Reads the case image from its attachment ``ref``.

    Returns:
        An :data:`Analyst`.
    """

    def analyst(
        case: Case, evidence: Evidence, pack: CountryPack, gate: GateResult
    ) -> AnalystResult | None:
        search = spark if gate.triage == "investigate" else None
        crop = None
        if segment and load and case.kind == "image" and case.attachments:
            crop = analyst_tools.crop_tool(load(case.attachments[0].ref), segment)
        tools = analyst_tools.toolbox(pack, suppliers, search, crop)
        return loop.investigate(case, evidence, tools, spark.complete)

    return analyst


def classify(case: Case, pack: CountryPack, gate: GateResult, analyst: Analyst | None) -> Verdict:
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


def judge_case(case: Case, pack: CountryPack, judge: Judge, analyst: Analyst | None) -> Verdict:
    """The gate on the case's own text, then :func:`classify`: one stateless message.

    The path of ``/caso``, ``measure.py`` and the WhatsApp webhook. The gate sees no chat
    history yet (chats are T-16).

    Args:
        case: The normalized case.
        pack: Country pack of the business, campaigns attached.
        judge: Jev, or its mock.
        analyst: The analyst; ``None`` means no model (degraded verdict).

    Returns:
        The verdict of specs/05.
    """
    return classify(case, pack, screen(case.text or "", [], pack, judge), analyst)
