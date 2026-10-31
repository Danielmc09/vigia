"""One case through the rules and, when they do not decide, the analyst (application).

The single pipeline of specs/09 for ``/caso``, ``measure.py`` and the adversarial runner:
signals → (analyst) → fusion. The gate runs before, per chat, because it needs history.
"""

import logging
import time
from collections.abc import Callable, Sequence

from vigia import telemetry
from vigia.analyst import loop
from vigia.domain.analyst import AnalystResult, Evidence
from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.gate import GateResult, Judge
from vigia.domain.image import Segmenter
from vigia.domain.member import Supplier
from vigia.domain.rejection import Rejection
from vigia.domain.signal import Signal
from vigia.domain.urls import Unrolled
from vigia.domain.verdict import Verdict, tools_usd
from vigia.jev.gate import screen
from vigia.model.spark import SparkClient
from vigia.services import analyst_tools
from vigia.signals.detector import detect
from vigia.signals.urls import Resolver, from_map, needs_unrolling
from vigia.verdict.build import build_verdict
from vigia.verdict.fusion import decided_by_rules, soften_citations

Analyst = Callable[[Case, Evidence, CountryPack, GateResult], AnalystResult | Rejection | None]
"""The analyst's read of a case, given what the rules and the gate already found.

Only a ``CONTENT_FILTER`` :class:`Rejection` adds :data:`FILTERED_SIGNAL`; any other degrades."""

FILTERED_SIGNAL = Signal(type="model_could_not_verify", weight="light", source="analyst")
"""The model's content filter refused the case (T-09b): informs, never decides."""

log = logging.getLogger(__name__)

UNROLL: Resolver | None = None
"""Short-link follower, set once at startup (D-099); ``None`` keeps the rules offline."""

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
    ) -> AnalystResult | Rejection | None:
        search = spark if gate.triage == "investigate" else None
        crop = None
        if segment and load and case.kind == "image" and case.attachments:
            crop = analyst_tools.crop_tool(load(case.attachments[0].ref), segment)
        tools = analyst_tools.toolbox(pack, suppliers, search, crop)
        tools["analyze_url"] = analyst_tools.analyze_url_tool(pack, evidence.links)  # D-101
        return loop.investigate(case, evidence, tools, spark.complete)

    return analyst


def _unrolled(case: Case) -> dict[str, Unrolled]:
    """The case's short links, followed before the rules run; a failure just leaves one out."""
    links = {}
    for url in case.urls:
        if UNROLL is not None and needs_unrolling(url):
            try:
                links[url] = UNROLL(url)
            except Exception as exc:  # noqa: BLE001 - never an error on the case path (D-099)
                log.info("unroll_failed %s", type(exc).__name__)  # the link itself is case content
    return links


def classify(case: Case, pack: CountryPack, gate: GateResult, analyst: Analyst | None) -> Verdict:
    """Signals, then the analyst only if the rules did not already decide, then fusion.

    Args:
        case: The normalized case.
        pack: Country pack of the business, campaigns attached.
        gate: What the gate (Jev and its rules floor) said about the chat.
        analyst: The analyst; ``None`` means no model (degraded verdict).

    A content-filter refusal, by a reader (``case.model_refused``) or by the analyst, leaves
    the verdict degraded with the light signal :data:`FILTERED_SIGNAL`, never an error (T-09b).

    Returns:
        The verdict of specs/05.
    """
    with telemetry.stage("rules"):
        links = _unrolled(case)
        signals = detect(case, pack, resolver=from_map(links))
    if case.model_refused:
        signals, analyst = [*signals, FILTERED_SIGNAL], None
    evidence = Evidence(
        signals=signals, manipulation_detected=gate.manipulation_detected, links=links
    )
    if analyst is not None and not decided_by_rules(soften_citations(signals, case, pack)):
        with telemetry.stage("analyst") as cost:
            read = analyst(case, evidence, pack, gate)
            cost["usd"] = tools_usd(read.tools_used) if isinstance(read, AnalystResult) else 0.0
        if isinstance(read, Rejection) and read.error_code == "CONTENT_FILTER":
            evidence = evidence.model_copy(update={"signals": [*signals, FILTERED_SIGNAL]})
        elif not isinstance(read, Rejection):
            evidence = evidence.model_copy(update={"analyst": read})
    with telemetry.stage("fusion"):
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
    business = "eval" if case.origin == "eval" else str(case.business_id)
    with telemetry.scope(case_id=str(case.id), business_id=str(case.business_id)):
        start = time.perf_counter()
        with telemetry.stage("jev"):
            gate = screen(case.text or "", [], pack, judge)
        verdict = classify(case, pack, gate, analyst)
        ms = (time.perf_counter() - start) * 1000
        telemetry.count_case(verdict, business, ms)
        return verdict
