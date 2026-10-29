"""T-09b incident adv-filtro-contenido: Spark's content filter degrades, never breaks.

Discord #meta (sondeo 2026-10-28): ``muse-spark-1.3`` answers ``400 content_policy_violation``
to plain security text. The adapter turns it into ``CONTENT_FILTER``; the analyst, the judge
and the PDF and image readers fall back to a deterministic verdict with the light signal
``model_could_not_verify``. Same tests for ``MODEL_BACKEND=spark`` and ``spark_open``.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import measure
import pytest

from vigia.analyst import loop
from vigia.config import Settings
from vigia.domain.analyst import Evidence, ModelRequest
from vigia.domain.case import Case
from vigia.domain.gate import MOCK_ANSWER, GateResult
from vigia.domain.reading import Transcript
from vigia.domain.rejection import Rejection
from vigia.i18n import LANGUAGES, t
from vigia.model import spark
from vigia.model.spark import SparkClient
from vigia.model.spark_judge import SparkJudge
from vigia.services import analyst_tools, process_case, transcription
from vigia.services.backend import make_chat
from vigia.signals.country_pack import country_pack

FIXTURE = Path(__file__).parent / "fixtures" / "meta" / "chat_content_filter.json"
CO = country_pack("CO")
assert CO is not None
GATE = GateResult(manipulation_detected=False, triage="investigate", source="mock")
VSAQ = "Are all of the systems used in this project scanned for host-level vulnerabilities?"
FILTERED = Rejection(error_code="CONTENT_FILTER", message="content_policy_violation")
BACKENDS = [spark.META, spark.SPARK_OPEN]


def _filtered(_: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json=json.loads(FIXTURE.read_text("utf-8"))["response"])


def _client(endpoint: spark.Endpoint, handler: Any = _filtered) -> SparkClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return SparkClient("k", http, endpoint)


def _request() -> ModelRequest:
    return ModelRequest(
        messages=[{"role": "user", "content": VSAQ}],
        output_schema=loop.output_schema(),
        timeout_s=5,
    )


def _case(text: str = VSAQ, **fields: Any) -> Case:
    return Case(
        id=uuid4(),
        business_id=uuid4(),
        country="CO",
        origin="eval",
        kind="text",
        text=text,
        received_at=datetime(2026, 10, 28, tzinfo=UTC),
        **fields,
    )


def _signal_types(verdict: Any) -> set[str]:
    return {s.type for s in verdict.signals}


# --- adapter: the 400 is a typed rejection, on both backends -----------------------------


@pytest.mark.parametrize("endpoint", BACKENDS, ids=["spark", "spark_open"])
def test_content_policy_400_is_a_content_filter_rejection(endpoint: spark.Endpoint) -> None:
    turn = _client(endpoint).complete(_request())

    assert isinstance(turn, Rejection)
    assert turn.error_code == "CONTENT_FILTER" and turn.retryable is False


@pytest.mark.parametrize("field", ["code", "type"])
def test_the_filter_is_recognised_in_either_error_field(field: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        error = {"code": None, "type": "invalid_request_error", "message": "flagged"}
        return httpx.Response(400, json={"error": error | {field: "content_policy_violation"}})

    turn = _client(spark.META, handler).complete(_request())

    assert isinstance(turn, Rejection) and turn.error_code == "CONTENT_FILTER"


def test_any_other_400_stays_model_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        error = {"code": None, "type": "invalid_request_error", "message": "bad tools"}
        return httpx.Response(400, json={"error": error})

    turn = _client(spark.META, handler).complete(_request())

    assert isinstance(turn, Rejection) and turn.error_code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize("endpoint", BACKENDS, ids=["spark", "spark_open"])
def test_each_backend_posts_to_its_own_host_and_model(endpoint: spark.Endpoint) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        content = json.dumps({"text": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    turn = _client(endpoint, handler).complete(_request())

    assert not isinstance(turn, Rejection) and turn.model == endpoint.models[0]
    assert str(seen[0].url) == f"{endpoint.base}/chat/completions"
    assert json.loads(seen[0].content)["model"] == endpoint.models[0]


def test_spark_open_falls_back_to_the_short_slug_on_model_not_found() -> None:
    """Discord: some accounts get 404 on ``muse-spark-open-1026`` and must use the short slug."""
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == spark.SPARK_OPEN.models[0]:
            error = {"code": "model_not_found", "type": "invalid_request_error", "message": "no"}
            return httpx.Response(404, json={"error": error})
        content = json.dumps({"text": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = _client(spark.SPARK_OPEN, handler)

    first, second = client.complete(_request()), client.complete(_request())

    assert not isinstance(first, Rejection) and first.model == "meta-models/muse-spark-open"
    assert not isinstance(second, Rejection)
    assert models == [*spark.SPARK_OPEN.models, spark.SPARK_OPEN.models[1]]


# --- backend choice is wiring (services), never an adapter importing another --------------


def test_make_chat_defaults_to_meta_spark() -> None:
    client = make_chat(Settings(_env_file=None, MODEL_API_KEY="k"))

    assert client is not None and client.endpoint == spark.META


def test_make_chat_spark_open_uses_baseten_and_its_key() -> None:
    config = Settings(
        _env_file=None, MODEL_BACKEND="spark_open", BASETEN_API_KEY="b", MODEL_API_KEY="k"
    )

    client = make_chat(config)

    assert client is not None and client.endpoint == spark.SPARK_OPEN


def test_make_chat_spark_open_without_its_key_is_none() -> None:
    config = Settings(_env_file=None, MODEL_BACKEND="spark_open", MODEL_API_KEY="k")

    assert make_chat(config) is None


# --- analyst: a filtered turn is a degraded verdict with the signal ------------------------


def test_the_loop_hands_the_content_filter_back_to_its_caller() -> None:
    result = loop.investigate(
        _case(), Evidence(), analyst_tools.toolbox(CO, (), None), lambda _: FILTERED
    )

    assert result == FILTERED


@pytest.mark.parametrize("endpoint", BACKENDS, ids=["spark", "spark_open"])
@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_adv_filtro_contenido_analyst_degrades_with_the_signal(
    endpoint: spark.Endpoint, lang: str
) -> None:
    analyst = process_case.spark_analyst(_client(endpoint))

    verdict = process_case.classify(_case(), CO, GATE, analyst)

    assert verdict.degraded is True and verdict.label == "doubtful"
    assert "model_could_not_verify" in _signal_types(verdict)
    assert t(lang, "signal.model_could_not_verify")


def test_the_explanation_names_the_signal_from_i18n_only() -> None:
    verdict = process_case.classify(_case(), CO, GATE, lambda *_: FILTERED)

    assert t("es", "signal.model_could_not_verify") in verdict.user_explanation
    assert "content_policy_violation" not in verdict.model_dump_json()


def test_other_analyst_failures_add_no_signal() -> None:
    verdict = process_case.classify(_case(), CO, GATE, lambda *_: None)

    assert verdict.degraded is True
    assert "model_could_not_verify" not in _signal_types(verdict)


# --- judge: a filtered judge is the mock, never an exception ------------------------------


@pytest.mark.parametrize("endpoint", BACKENDS, ids=["spark", "spark_open"])
def test_adv_filtro_contenido_judge_falls_back_to_mock(endpoint: spark.Endpoint) -> None:
    assert SparkJudge(_client(endpoint))(f"<last_turn>{VSAQ}</last_turn>") == MOCK_ANSWER


# --- readers: a filtered PDF or image becomes an empty, refused transcript -----------------

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def test_a_filtered_pdf_is_a_refused_transcript() -> None:
    read = transcription.read_pdf(PDF, "es", lambda *_: FILTERED, 1024)

    assert read == Transcript(text="", usd=0.0, refused=True)


def test_a_filtered_image_keeps_its_qr_links() -> None:
    reader = transcription.image_reader(lambda *_: FILTERED, None)

    read = reader(b"not an image, no qr", "es")

    assert isinstance(read, Transcript) and read.refused is True and read.text == ""


def test_a_refused_case_skips_the_analyst_and_carries_the_signal() -> None:
    calls: list[Case] = []

    def analyst(case: Case, *_: object) -> None:
        calls.append(case)

    verdict = process_case.classify(_case("", model_refused=True), CO, GATE, analyst)

    assert calls == [] and verdict.degraded is True
    assert "model_could_not_verify" in _signal_types(verdict)


def test_measure_carries_the_refusal_into_the_case() -> None:
    raw = {"id": "c-x", "pais": "CO", "tipo": "texto", "fecha": "2026-10-28"}
    heard = Transcript(text="", usd=0.0, refused=True)

    assert measure.to_case(raw, heard=heard).model_refused is True
    assert measure.to_case(raw).model_refused is False
