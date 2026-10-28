"""T-13 PDF: magic and size check, Spark reads the text, read step and eval wiring (D-071)."""

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import measure
import pytest

from vigia.config import Settings
from vigia.domain.attachment import check_pdf
from vigia.domain.reading import Transcript
from vigia.domain.rejection import Rejection
from vigia.i18n import LANGUAGES, has_key
from vigia.model.spark import PDF_MAX_TOKENS, SparkClient, usage_usd
from vigia.services import transcription

FIXTURE = Path(__file__).parent / "fixtures" / "meta" / "chat_pdf.json"
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


# --- PDF check (domain) -------------------------------------------------------------------


def test_a_pdf_under_the_cap_passes() -> None:
    assert check_pdf(PDF, max_bytes=1024) is None


@pytest.mark.parametrize(
    "data",
    [b"", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, b"PDF-1.4 no percent", b" %PDF-1.4"],
    ids=["empty", "png", "no-percent", "leading-space"],
)
def test_anything_not_starting_with_the_pdf_magic_is_a_typed_rejection(data: bytes) -> None:
    result = check_pdf(data, max_bytes=1024)

    assert isinstance(result, Rejection)
    assert result.error_code == "INVALID_ATTACHMENT"


def test_a_pdf_over_the_cap_is_refused() -> None:
    result = check_pdf(PDF + b"\x00" * 1024, max_bytes=1024)

    assert isinstance(result, Rejection)
    assert result.error_code == "TOO_LARGE"


def test_pdf_cap_comes_from_config() -> None:
    assert Settings().pdf_max_bytes == 5 * 1024 * 1024
    assert Settings(VIGIA_PDF_MAX_BYTES=1000).pdf_max_bytes == 1000


# --- Spark reads the PDF (model/spark.py) -------------------------------------------------


def _fixture_response(content: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(FIXTURE.read_text("utf-8"))["response"]
    body["choices"][0]["message"]["content"] = content
    return body


def _spark(response: httpx.Response, seen: list[httpx.Request]) -> SparkClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return SparkClient("k", httpx.Client(transport=httpx.MockTransport(handler)))


def test_spark_reads_the_pdf_as_a_file_part_with_a_text_schema() -> None:
    body = _fixture_response(json.dumps({"text": "Factura 0042. Pague hoy en pagos-ejemplo.test"}))
    seen: list[httpx.Request] = []
    spark = _spark(httpx.Response(200, json=body), seen)

    result = spark.read_pdf(PDF, "es")

    usd, _ = usage_usd(body["usage"])
    assert result == Transcript(text="Factura 0042. Pague hoy en pagos-ejemplo.test", usd=usd)
    assert spark.usd == pytest.approx(usd)
    sent = json.loads(seen[0].content)
    assert seen[0].url == "https://api.meta.ai/v1/chat/completions"
    parts = sent["messages"][0]["content"]
    file_part = next(p for p in parts if p["type"] == "file")
    assert file_part["file"]["file_data"] == (
        "data:application/pdf;base64," + base64.b64encode(PDF).decode()
    )
    schema = sent["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"] == {"text": {"type": "string"}}
    assert schema["name"] != "Veredicto"
    assert sent["max_completion_tokens"] == PDF_MAX_TOKENS
    assert "tools" not in sent


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(200, json=_fixture_response("Factura 0042 sin JSON")),
        httpx.Response(200, json=_fixture_response(json.dumps({"texto": "x"}))),
    ],
    ids=["http-500", "free-text", "wrong-key"],
)
def test_pdf_read_failure_is_a_retryable_rejection_not_an_exception(
    response: httpx.Response,
) -> None:
    result = _spark(response, []).read_pdf(PDF, "es")

    assert isinstance(result, Rejection)
    assert result.error_code == "MODEL_UNAVAILABLE"
    assert result.retryable


# --- read step (services) -----------------------------------------------------------------


class FakeReader:
    """Answers a fixed text and keeps what it was asked."""

    def __init__(self, text: str = "Factura") -> None:
        self.calls: list[tuple[bytes, str]] = []
        self.result = Transcript(text=text, usd=0.004)

    def __call__(self, pdf: bytes, language: str) -> Transcript | Rejection:
        self.calls.append((pdf, language))
        return self.result


def test_read_pdf_checks_the_file_before_spending() -> None:
    reader = FakeReader()

    result = transcription.read_pdf(b"not a pdf", "es", reader, max_bytes=1024)

    assert isinstance(result, Rejection) and result.error_code == "INVALID_ATTACHMENT"
    assert reader.calls == []


def test_read_pdf_passes_a_valid_pdf_to_the_reader() -> None:
    reader = FakeReader("hello")

    assert transcription.read_pdf(PDF, "en", reader, max_bytes=1024) == reader.result
    assert reader.calls == [(PDF, "en")]


@pytest.mark.parametrize("code", ["INVALID_ATTACHMENT", "TOO_LARGE", "MODEL_UNAVAILABLE"])
@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_pdf_rejection_has_a_user_message(code: str, lang: str) -> None:
    rejection = Rejection(error_code=code, message="x")  # type: ignore[arg-type]

    key = transcription.PDF_REPLY_KEY[rejection.error_code]
    assert key.startswith("pdf.") and has_key(lang, key)
    assert transcription.reply(rejection, lang, transcription.PDF_REPLY_KEY) == (
        transcription.t(lang, key)
    )


# --- eval wiring (tools/measure.py) -------------------------------------------------------


def _pdf(case_id: str, expected: str, tmp_path: Path, pais: str = "CO") -> dict[str, object]:
    (tmp_path / f"{case_id}.pdf").write_bytes(PDF)
    return {
        "id": case_id,
        "tipo": "pdf",
        "pais": pais,
        "fuente": f"{case_id}.pdf",
        "esperado": expected,
        "categoria": "bec_proveedor" if expected == "estafa" else "legitimo_proveedor",
        "hipotesis": "h",
        "autor": "claude",
        "fecha": "2026-10-27",
        "origen": "sintetico",
    }


def test_to_case_reads_the_pdf_into_the_text_and_keeps_the_kind(tmp_path: Path) -> None:
    reader = FakeReader("Factura vencida: pague en https://dian-pagos.co/x hoy")
    raw = _pdf("p-1", "estafa", tmp_path, pais="US")

    heard, _ = measure._hear(raw, tmp_path, {"pdf": reader})
    case = measure.to_case(raw, tmp_path, heard)

    assert case.kind == "pdf"
    assert case.text == reader.result.text
    assert case.urls == ["https://dian-pagos.co/x"]
    assert reader.calls[0][1] == "en"


def test_pdf_reading_cost_is_billed_to_the_case(tmp_path: Path) -> None:
    raw = _pdf("p-1", "estafa", tmp_path)

    def classify(case: object) -> measure.Outcome:
        return measure.Outcome(label="scam", usd=0.01)

    record = measure.grade(raw, classify, tmp_path, {"pdf": FakeReader()})

    assert record["paso"] is True
    assert record["usd"] == pytest.approx(0.014)
    assert record["herramientas_usadas"][0]["name"] == "read_pdf"


def test_a_rejected_pdf_is_a_failed_case_not_a_crash(tmp_path: Path) -> None:
    raw = _pdf("p-1", "estafa", tmp_path)
    (tmp_path / "p-1.pdf").write_bytes(b"GIF89a")

    reader = FakeReader()
    record = measure.grade(raw, lambda _: measure.Outcome(label="scam"), tmp_path, {"pdf": reader})

    assert record["paso"] is False
    assert "INVALID_ATTACHMENT" in record["error"]
    assert reader.calls == []


def test_rules_only_mode_skips_pdfs_needing_reading_with_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = {**_pdf("t-1", "estafa", tmp_path), "tipo": "texto", "texto": "Hola"}
    del text["fuente"]
    path = tmp_path / "casos.jsonl"
    cases = [text, _pdf("p-1", "estafa", tmp_path)]
    path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")

    measure.main(
        [str(path), "--solo-deterministas", "--json", str(tmp_path / "o.json")], out_dir=tmp_path
    )

    result = json.loads((tmp_path / "o.json").read_text("utf-8"))
    assert [c["id"] for c in result["casos"]] == ["t-1"]
    assert result["omitidos"] == [{"id": "p-1", "motivo": measure.NO_PDF}]
    assert "SKIP p-1" in capsys.readouterr().out


def test_every_pdf_eval_sample_passes_the_check() -> None:
    root = Path(__file__).parents[1]
    cases = [
        json.loads(line)
        for line in (root / "evals" / "casos.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    pdfs = [case for case in cases if case["tipo"] == "pdf"]

    assert len(pdfs) >= 10
    assert sum(case["esperado"] == "legitimo" for case in pdfs) >= 4
    assert sum(case["pais"] == "US" for case in pdfs) >= 2
    for case in pdfs:
        assert check_pdf((root / case["fuente"]).read_bytes(), max_bytes=5_000_000) is None
