"""T-11 Muse Voice: canonical WAV check, Voice adapter, transcription step and eval wiring."""

import io
import json
import wave
from pathlib import Path

import httpx
import measure
import pytest

from vigia.config import Settings
from vigia.domain.attachment import MAX_ATTACHMENT_BYTES, check_wav
from vigia.domain.reading import Transcript
from vigia.domain.rejection import Rejection
from vigia.i18n import LANGUAGES, has_key
from vigia.model.voice import PRICE_HOUR, VoiceClient, make_voice
from vigia.services import transcription

FIXTURE = Path(__file__).parent / "fixtures" / "meta" / "asr_transcribe.json"


def _wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buf.getvalue()


# --- canonical WAV check (domain) --------------------------------------------------------


def test_canonical_wav_passes_with_its_duration() -> None:
    assert check_wav(_wav(2.5), max_seconds=120) == pytest.approx(2.5)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"OggS\x00\x02" + b"\x00" * 100,  # WhatsApp voice note (ogg/opus): no ffmpeg yet
        _wav(rate=8000),
        _wav(channels=2),
        _wav(width=3),
        _wav(seconds=0),
    ],
    ids=["empty", "ogg", "8khz", "stereo", "24bit", "no-frames"],
)
def test_anything_but_canonical_wav_is_a_typed_rejection(data: bytes) -> None:
    result = check_wav(data, max_seconds=120)

    assert isinstance(result, Rejection)
    assert result.error_code == "UNSUPPORTED_AUDIO"


def test_audio_longer_than_the_cap_is_refused() -> None:
    result = check_wav(_wav(3), max_seconds=2)

    assert isinstance(result, Rejection)
    assert result.error_code == "AUDIO_TOO_LONG"


def test_audio_over_the_attachment_size_cap_is_refused_before_parsing() -> None:
    result = check_wav(b"RIFF" + b"\x00" * MAX_ATTACHMENT_BYTES, max_seconds=10_000)

    assert isinstance(result, Rejection)
    assert result.error_code == "TOO_LARGE"


# --- Voice adapter (model/voice.py) -------------------------------------------------------


def _voice(response: httpx.Response, seen: list[httpx.Request]) -> VoiceClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return VoiceClient("k", httpx.Client(transport=httpx.MockTransport(handler)))


def test_voice_posts_the_verified_multipart_and_bills_by_audio_duration() -> None:
    body = json.loads(FIXTURE.read_text("utf-8"))["response"]
    seen: list[httpx.Request] = []
    wav = _wav(1)

    result = _voice(httpx.Response(200, json=body), seen)(wav, "es")

    assert result == Transcript(
        text=body["transcript"], audio_ms=4320, usd=4320 / 3_600_000 * PRICE_HOUR
    )
    request = seen[0]
    assert request.url.path == "/v1/asr/transcribe"
    assert len(request.url.params["sessionId"]) == 36
    assert request.headers["authorization"] == "Bearer k"
    content = request.read()
    assert b'name="request"' in content and b'name="audio"' in content
    assert b'"model": "muse-voice-transcribe-1.0"' in content
    assert b'"audioEncoding": "WAV"' in content and b'"languageBias": ["es"]' in content
    assert wav in content


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, json={}), httpx.Response(200, json={"sessionId": "x"})],
    ids=["http-500", "no-transcript"],
)
def test_voice_failure_is_a_retryable_rejection_not_an_exception(
    response: httpx.Response,
) -> None:
    result = _voice(response, [])(_wav(1), "es")

    assert isinstance(result, Rejection)
    assert result.error_code == "MODEL_UNAVAILABLE"
    assert result.retryable


def test_no_voice_without_a_model_key() -> None:
    assert make_voice(Settings(MODEL_API_KEY=None)) is None
    assert isinstance(make_voice(Settings(MODEL_API_KEY="k")), VoiceClient)


def test_max_duration_comes_from_config() -> None:
    assert Settings().voice_max_seconds == 120
    assert Settings(VIGIA_VOICE_MAX_SECONDS=30).voice_max_seconds == 30


# --- transcription step (services) --------------------------------------------------------


class FakeVoice:
    """Answers a fixed transcript and keeps what it was asked."""

    def __init__(self, text: str = "hola", audio_ms: int = 2000) -> None:
        self.calls: list[tuple[bytes, str]] = []
        self.result = Transcript(text=text, audio_ms=audio_ms, usd=0.0001)

    def __call__(self, wav: bytes, language: str) -> Transcript | Rejection:
        self.calls.append((wav, language))
        return self.result


def test_transcribe_checks_the_wav_before_spending() -> None:
    voice = FakeVoice()

    result = transcription.transcribe(b"OggS", "es", voice, max_seconds=120)

    assert isinstance(result, Rejection) and result.error_code == "UNSUPPORTED_AUDIO"
    assert voice.calls == []


def test_transcribe_passes_the_language_bias_to_voice() -> None:
    voice = FakeVoice("hello")
    wav = _wav(1)

    assert transcription.transcribe(wav, "en", voice, max_seconds=120) == voice.result
    assert voice.calls == [(wav, "en")]


@pytest.mark.parametrize(
    "code", ["UNSUPPORTED_AUDIO", "AUDIO_TOO_LONG", "TOO_LARGE", "MODEL_UNAVAILABLE"]
)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_audio_rejection_has_a_user_message(code: str, lang: str) -> None:
    rejection = Rejection(error_code=code, message="x")  # type: ignore[arg-type]

    assert has_key(lang, transcription.REPLY_KEY[rejection.error_code])
    assert transcription.reply(rejection, lang)


# --- eval wiring (tools/measure.py) -------------------------------------------------------


def _audio(case_id: str, expected: str, tmp_path: Path, pais: str = "CO") -> dict[str, object]:
    (tmp_path / f"{case_id}.wav").write_bytes(_wav(1))
    return {
        "id": case_id,
        "tipo": "audio",
        "pais": pais,
        "fuente": f"{case_id}.wav",
        "esperado": expected,
        "categoria": "banco" if expected == "estafa" else "legitimo_otro",
        "hipotesis": "h",
        "autor": "claude",
        "fecha": "2026-10-27",
        "origen": "sintetico",
    }


def test_to_case_transcribes_audio_into_the_text_and_keeps_the_kind(tmp_path: Path) -> None:
    voice = FakeVoice("Bancolombia: verifique en https://bancolombia-seguro.co/x ya")

    raw = _audio("a-1", "estafa", tmp_path, pais="US")
    heard, _ = measure._hear(raw, tmp_path, {"audio": voice})

    case = measure.to_case(raw, tmp_path, heard)

    assert case.kind == "audio"
    assert case.text == voice.result.text
    assert case.urls == ["https://bancolombia-seguro.co/x"]
    assert voice.calls[0][1] == "en"  # language bias from the country pack


def test_transcription_cost_and_time_are_billed_to_the_case(tmp_path: Path) -> None:
    raw = _audio("a-1", "estafa", tmp_path)
    voice = FakeVoice("una estafa")

    def classify(case: object) -> measure.Outcome:
        return measure.Outcome(label="scam", usd=0.01)

    record = measure.grade(raw, classify, tmp_path, {"audio": voice})

    assert record["paso"] is True
    assert record["usd"] == pytest.approx(0.0101)
    assert record["herramientas_usadas"][0]["name"] == "transcribe"


def test_a_rejected_audio_is_a_failed_case_not_a_crash(tmp_path: Path) -> None:
    raw = _audio("a-1", "estafa", tmp_path)
    (tmp_path / "a-1.wav").write_bytes(b"OggS" + b"\x00" * 64)

    record = measure.grade(
        raw, lambda _: measure.Outcome(label="scam"), tmp_path, {"audio": FakeVoice()}
    )

    assert record["paso"] is False
    assert "UNSUPPORTED_AUDIO" in record["error"]


def test_audio_without_a_country_pack_is_unheard_not_guessed_spanish(tmp_path: Path) -> None:
    raw = _audio("a-1", "estafa", tmp_path, pais="ZZ")
    voice = FakeVoice()

    record = measure.grade(raw, lambda _: measure.Outcome(label="scam"), tmp_path, {"audio": voice})

    assert record["paso"] is False
    assert "no country pack ZZ" in record["error"]
    assert voice.calls == []


def test_rules_only_mode_skips_audio_needing_transcription_with_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = {**_audio("t-1", "estafa", tmp_path), "tipo": "texto", "texto": "Hola"}
    del text["fuente"]
    path = tmp_path / "casos.jsonl"
    cases = [text, _audio("a-1", "estafa", tmp_path)]
    path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")

    measure.main(
        [str(path), "--solo-deterministas", "--json", str(tmp_path / "o.json")], out_dir=tmp_path
    )

    result = json.loads((tmp_path / "o.json").read_text("utf-8"))
    assert [c["id"] for c in result["casos"]] == ["t-1"]
    assert result["omitidos"] == [{"id": "a-1", "motivo": measure.NO_VOICE}]
    assert "SKIP a-1" in capsys.readouterr().out
