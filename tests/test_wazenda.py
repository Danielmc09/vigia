"""Wazenda adapter (specs/01): signature, payload, media download, sending; ogg → WAV."""

import hashlib
import hmac
import io
import json
import math
import struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import av
import httpx
import pytest

from vigia import telemetry
from vigia.channel.wazenda import Wazenda, ogg_to_wav, signed_payload, to_message
from vigia.domain.attachment import MAX_ATTACHMENT_BYTES, check_wav, sniff
from vigia.domain.rejection import Rejection

FIXTURES = Path(__file__).parent / "fixtures" / "wazenda"
SECRET = b"test-secret-not-real"
BASE = "https://wazenda.com/api/v1/public"
LINE = "00000000-0000-4000-8000-000000000001"
MEDIA = f"{BASE}/media/00000000-0000-4000-8000-00000000a003"
pytestmark = pytest.mark.anyio


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return data


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def opus(seconds: float = 2.0) -> bytes:
    """A synthetic mono ogg/opus voice note (a 440 Hz tone), as WhatsApp sends them."""
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        step, total, pts = 960, int(48000 * seconds), 0
        while pts < total:
            hz = 2 * math.pi * 440 / 48000
            tone = (int(8000 * math.sin(hz * (pts + i))) for i in range(step))
            frame = av.AudioFrame(format="s16", layout="mono", samples=step)
            frame.planes[0].update(b"".join(struct.pack("<h", s) for s in tone))
            frame.rate, frame.pts = 48000, pts
            for packet in stream.encode(frame):
                out.mux(packet)
            pts += step
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


# --- signature (specs/01 step 2) ------------------------------------------------------


def test_a_correct_signature_gives_the_payload() -> None:
    body = (FIXTURES / "texto.json").read_bytes()
    assert signed_payload(SECRET, body, sign(body)) == json.loads(body)


@pytest.mark.parametrize(
    "header",
    [None, "", "deadbeef", "md5=" + "0" * 64, "sha256=zz", "sha256=" + "0" * 64],
)
def test_a_missing_or_wrong_signature_is_refused(header: str | None) -> None:
    body = (FIXTURES / "texto.json").read_bytes()
    assert signed_payload(SECRET, body, header) is None


def test_a_body_changed_after_signing_is_refused() -> None:
    body = (FIXTURES / "texto.json").read_bytes()
    assert signed_payload(SECRET, body + b" ", sign(body)) is None


def test_invalid_json_with_a_good_signature_is_refused() -> None:
    body = b"{not json"
    assert signed_payload(SECRET, body, sign(body)) is None


def test_without_a_secret_nothing_is_accepted() -> None:
    body = (FIXTURES / "texto.json").read_bytes()
    assert signed_payload(None, body, sign(body)) is None


# --- payload (specs/01 steps 3-4) -----------------------------------------------------


def test_a_text_message_becomes_an_incoming_message() -> None:
    msg = to_message(fixture("texto"))
    assert msg is not None
    assert (msg.origin, msg.kind, msg.sender) == ("wazenda", "text", "+573000000001")
    assert msg.text and msg.text.startswith("Bancolombia")
    assert msg.message_id == fixture("texto")["message_id"]
    assert msg.attachments == []


def test_a_sender_without_plus_is_normalized() -> None:
    msg = to_message(fixture("sin_mas"))
    assert msg is not None and msg.sender == "+573000000001"


@pytest.mark.parametrize("name", ["audio", "pdf", "imagen"])
def test_media_kind_waits_for_the_bytes(name: str) -> None:
    raw = fixture(name)
    msg = to_message(raw)
    assert msg is not None
    assert msg.kind == "unknown"  # media_type decides nothing (specs/01)
    assert [a.ref for a in msg.attachments] == [raw["media_url"]]
    assert msg.attachments[0].mime is None


def test_a_null_body_with_media_has_no_text() -> None:
    msg = to_message(fixture("audio"))
    assert msg is not None and msg.text is None


@pytest.mark.parametrize(
    "raw",
    [
        fixture("status"),
        {k: v for k, v in fixture("texto").items() if k != "body"},
        fixture("texto") | {"body": None},
        fixture("texto") | {"from": None},
        {"event": "message.received"},
    ],
)
def test_events_to_ignore(raw: dict[str, Any]) -> None:
    assert to_message(raw) is None


def test_phones_are_masked_in_logs() -> None:
    assert telemetry.mask_phone("+573000000001") == "***0001"
    assert telemetry.mask_phone("12") == "***"


# --- media download (specs/01) --------------------------------------------------------


def client(handler: Any, **kwargs: Any) -> tuple[Wazenda, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    values: dict[str, Any] = {"sleep": sleep, "jitter": lambda: 0.25}
    wazenda = Wazenda(BASE, "key-not-real", LINE, http, **(values | kwargs))
    return wazenda, slept


async def test_media_is_downloaded_with_the_api_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"%PDF-1.4 x")

    wazenda, _ = client(handler)
    assert await wazenda.download(MEDIA) == b"%PDF-1.4 x"
    assert seen[0].headers["X-API-Key"] == "key-not-real"
    assert str(seen[0].url) == MEDIA


@pytest.mark.parametrize(
    "url",
    [
        "https://wazenda.com.evil.example/media/1",
        "https://wazenda.com@evil.example/media/1",
        "https://www.evil.example/media/1",
        "https://wwwwazenda.com/media/1",
        "https://user:pw@wazenda.com/media/1",
        "http://wazenda.com/api/v1/public/media/1",
        "https://wazenda.com:8443/media/1",
        "https://evil.example/?u=https://wazenda.com",
        "file:///etc/passwd",
        "not a url",
    ],
)
async def test_media_from_another_host_is_never_fetched(url: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    wazenda, _ = client(handler)
    result = await wazenda.download(url)
    assert isinstance(result, Rejection) and result.error_code == "INVALID_ATTACHMENT"
    assert seen == []


async def test_media_over_10_mb_stops_mid_stream_without_content_length() -> None:
    chunk = b"\0" * 1024 * 1024

    async def endless() -> Any:
        for _ in range(MAX_ATTACHMENT_BYTES // len(chunk) + 5):
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless())

    wazenda, _ = client(handler)
    result = await wazenda.download(MEDIA)
    assert isinstance(result, Rejection) and result.error_code == "TOO_LARGE"


@pytest.mark.parametrize("status", [302, 404, 500])
async def test_a_failed_download_is_a_retryable_rejection(status: int) -> None:
    wazenda, _ = client(lambda r: httpx.Response(status, headers={"Location": "https://x.example"}))
    result = await wazenda.download(MEDIA)
    assert isinstance(result, Rejection) and result.error_code == "CHANNEL_UNAVAILABLE"
    assert result.retryable


async def test_a_network_error_is_a_rejection_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    wazenda, _ = client(handler)
    result = await wazenda.download(MEDIA)
    assert isinstance(result, Rejection) and result.error_code == "CHANNEL_UNAVAILABLE"


# --- sending (specs/01 Salida) --------------------------------------------------------


async def test_t18b_send_template_posts_name_language_and_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    wazenda, _ = client(handler)
    params = ["Ferreteria", "Luis", "Cómo reconocerla: link falso.", "3"]
    assert await wazenda.send_template("+573000000001", "alerta_equipo_estafa", "es", params)
    assert str(seen[0].url) == f"{BASE}/templates"
    assert seen[0].headers["X-API-Key"] == "key-not-real"
    assert json.loads(seen[0].content) == {
        "line_id": LINE,
        "number": "+573000000001",
        "template_name": "alerta_equipo_estafa",
        "language": "es",
        "body_params": params,
    }


async def test_send_posts_line_number_and_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    wazenda, slept = client(handler)
    assert await wazenda.send("+573000000001", "hola " + "x" * 800)
    assert str(seen[0].url) == f"{BASE}/messages"
    assert seen[0].method == "POST"
    assert seen[0].headers["X-API-Key"] == "key-not-real"
    body = json.loads(seen[0].content)
    assert body["line_id"] == LINE and body["number"] == "+573000000001"
    assert len(body["message"]) == 700 and body["message"].startswith("hola ")
    assert slept == []


async def test_429_is_retried_with_backoff_and_jitter() -> None:
    answers = iter([429, 429, 200])
    wazenda, slept = client(lambda r: httpx.Response(next(answers)))
    assert await wazenda.send("+573000000001", "hola")
    assert slept == [1.25, 2.25]


async def test_429_gives_up_after_three_retries() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429)

    wazenda, slept = client(handler)
    assert not await wazenda.send("+573000000001", "hola")
    assert len(calls) == 4 and slept == [1.25, 2.25, 4.25]


async def test_a_send_error_returns_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    wazenda, _ = client(handler)
    assert not await wazenda.send("+573000000001", "hola")
    wazenda, _ = client(lambda r: httpx.Response(500))
    assert not await wazenda.send("+573000000001", "hola")


async def test_own_rate_cap_waits_before_reaching_the_429() -> None:
    now = [100.0]
    wazenda, slept = client(lambda r: httpx.Response(200), per_minute=2, clock=lambda: now[0])
    for _ in range(3):
        assert await wazenda.send("+573000000001", "hola")
    assert slept == [60.0]  # the third waits for the first to leave the minute window


# --- attachment bytes -----------------------------------------------------------------


def test_sniff_reads_the_kind_from_magic_bytes() -> None:
    wav = io.BytesIO()
    with __import__("wave").open(wav, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 160)
    assert sniff(b"\xff\xd8\xff\xe0rest") == ("image", "image/jpeg")
    assert sniff(b"%PDF-1.7 rest") == ("document", "application/pdf")
    assert sniff(b"OggS\0rest") == ("audio", "audio/ogg")
    assert sniff(wav.getvalue()) == ("audio", "audio/wav")
    assert sniff(b"PK\x03\x04zip") is None
    assert sniff(b"") is None


def test_an_opus_voice_note_becomes_canonical_wav() -> None:
    wav = ogg_to_wav(opus(2.0), max_seconds=120)
    assert isinstance(wav, bytes)
    seconds = check_wav(wav, 120)
    assert isinstance(seconds, float) and 1.9 <= seconds <= 2.1


def test_a_long_voice_note_stops_decoding_at_the_cap() -> None:
    result = ogg_to_wav(opus(3.0), max_seconds=1)
    assert isinstance(result, Rejection) and result.error_code == "AUDIO_TOO_LONG"


def test_a_broken_ogg_is_unsupported_audio() -> None:
    result = ogg_to_wav(b"OggS" + b"\0" * 100, max_seconds=120)
    assert isinstance(result, Rejection) and result.error_code == "UNSUPPORTED_AUDIO"


# --- T-10e: media by listing (specs/01, "Descarga de adjuntos", 31 oct) ---------------

WAMID = "wamid.SYNTHETIC0007"
VOICE_ID = "00000000-0000-4000-8000-00000000b001"


@pytest.mark.parametrize("body", ["[audio]", "[image]", "[document]"])
def test_t10e_a_placeholder_body_is_media_not_text(body: str) -> None:
    """T-10e incident: the real media webhook brings `[audio]` and no media_url."""
    msg = to_message(fixture("audio_sin_url") | {"body": body})
    assert msg is not None
    assert msg.kind == "unknown" and msg.text is None
    assert [a.ref for a in msg.attachments] == [""]  # empty ref: fetch it by listing


def test_t10e_a_media_type_alone_is_media() -> None:
    msg = to_message(fixture("texto") | {"body": None, "media_type": "audio"})
    assert msg is not None and msg.kind == "unknown" and len(msg.attachments) == 1


def _listing(items: list[dict[str, Any]], media: bytes = b"", status: int = 200) -> Any:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(status, json=items)
        return httpx.Response(200, content=media)

    return handler, seen


def _webhook_time() -> Any:
    msg = to_message(fixture("audio_sin_url"))
    assert msg is not None
    return msg.received_at


async def test_t10e_the_attachment_is_found_by_listing_and_fetched_by_uuid() -> None:
    """T-10e incident: the webhook sends a wamid, the media endpoint wants Wazenda's uuid."""
    voice = opus()
    items = [i | {"media_url": None} for i in fixture("mensajes_media")["response"]]
    handler, seen = _listing(items, voice)
    wazenda, _ = client(handler)

    assert await wazenda.latest_media("+573000000001", _webhook_time()) == voice
    listing, media = seen
    assert listing.url.params["contact"] == "573000000001"  # digits, as contact_number
    assert listing.url.params["line_id"] == LINE
    assert str(media.url) == f"{BASE}/messages/{VOICE_ID}/media"
    assert media.headers["X-API-Key"] == "key-not-real"
    assert all(WAMID not in str(r.url) for r in seen)


def _voice(**over: Any) -> dict[str, Any]:
    item: dict[str, Any] = fixture("mensajes_media")["response"][0]
    return item | over


@pytest.mark.parametrize(
    "items",
    [
        [],
        [_voice(), _voice(id="00000000-0000-4000-8000-00000000b009")],
        [_voice(created_at="2026-10-31T13:50:00Z")],
        [_voice(contact_number="573000000002")],
        [_voice(direction="outbound")],
        [_voice(id="wamid.SYNTHETIC0007")],
        [_voice(media_type=None)],
    ],
    ids=["none", "two", "too-old", "other-contact", "outbound", "not-a-uuid", "text"],
)
async def test_t10e_without_a_clear_match_nothing_is_guessed(items: list[dict[str, Any]]) -> None:
    handler, seen = _listing(items, opus())
    wazenda, _ = client(handler)

    result = await wazenda.latest_media("+573000000001", _webhook_time())
    assert isinstance(result, Rejection) and result.error_code == "MEDIA_MATCH_FAILED"
    assert len(seen) == 1  # the listing only; no media is fetched


async def test_t10e_a_listing_that_is_not_a_list_does_not_match() -> None:
    handler, _ = _listing({"detail": "x"})  # type: ignore[arg-type]
    wazenda, _ = client(handler)
    result = await wazenda.latest_media("+573000000001", _webhook_time())
    assert isinstance(result, Rejection) and result.error_code == "MEDIA_MATCH_FAILED"


async def test_t10e_a_failed_listing_is_retryable() -> None:
    handler, _ = _listing([], status=500)
    wazenda, _ = client(handler)
    result = await wazenda.latest_media("+573000000001", _webhook_time())
    assert isinstance(result, Rejection) and result.error_code == "CHANNEL_UNAVAILABLE"
    assert result.retryable


async def test_t10e_old_media_502_is_retryable() -> None:
    """Provider doc: media older than its retention answers 502."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=fixture("mensajes_media")["response"])
        return httpx.Response(502)

    wazenda, _ = client(handler)
    result = await wazenda.latest_media("+573000000001", _webhook_time())
    assert isinstance(result, Rejection) and result.error_code == "CHANNEL_UNAVAILABLE"


async def test_t10e_a_huge_attachment_stops_at_10_mb() -> None:
    chunk = b"\0" * 1024 * 1024

    async def endless() -> Any:
        for _ in range(MAX_ATTACHMENT_BYTES // len(chunk) + 5):
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=fixture("mensajes_media")["response"])
        return httpx.Response(200, content=endless())

    wazenda, _ = client(handler)
    result = await wazenda.latest_media("+573000000001", _webhook_time())
    assert isinstance(result, Rejection) and result.error_code == "TOO_LARGE"


# --- Incident 31 oct: a second attachment within two minutes always failed --------------

IMAGE_ID = "00000000-0000-4000-8000-00000000b004"


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _pair(seconds_apart: int) -> list[dict[str, Any]]:
    """A voice note at 14:08:35 and an image ``seconds_apart`` later, newest first."""
    image_at = _at("2026-10-31T14:08:35+00:00") + timedelta(seconds=seconds_apart)
    return [
        _voice(id=IMAGE_ID, media_type="image", body="[image]", media_url=None)
        | {"created_at": image_at.isoformat()},
        _voice(media_url=None, created_at="2026-10-31T14:08:35Z"),
    ]


@pytest.mark.parametrize(
    ("webhook", "expected"),
    [("2026-10-31T14:08:35+00:00", VOICE_ID), ("2026-10-31T14:10:04+00:00", IMAGE_ID)],
    ids=["voice", "image"],
)
async def test_two_attachments_89_s_apart_each_matches_its_own(webhook: str, expected: str) -> None:
    """Incident 31 oct: voice 14:08:35, image 14:10:04; the image got MEDIA_MATCH_FAILED."""
    handler, seen = _listing(_pair(89), b"bytes")
    wazenda, _ = client(handler)

    assert await wazenda.latest_media("+573000000001", _at(webhook)) == b"bytes"
    assert str(seen[-1].url) == f"{BASE}/messages/{expected}/media"


async def test_two_attachments_2_s_apart_are_not_guessed() -> None:
    handler, seen = _listing(_pair(2), b"bytes")
    wazenda, _ = client(handler)

    result = await wazenda.latest_media("+573000000001", _at("2026-10-31T14:08:36+00:00"))
    assert isinstance(result, Rejection) and result.error_code == "MEDIA_MATCH_FAILED"
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("webhook", "expected"),
    [("2026-10-31T14:08:38+00:00", None), ("2026-10-31T14:08:37+00:00", VOICE_ID)],
    ids=["gap-4s-rejected", "gap-6s-nearest"],
)
async def test_the_ambiguity_gap_boundary(webhook: str, expected: str | None) -> None:
    """Candidates 3 s/7 s away (gap 4 s) are not told apart; 2 s/8 s (gap 6 s) are (D-103)."""
    handler, seen = _listing(_pair(10), b"bytes")
    wazenda, _ = client(handler)

    result = await wazenda.latest_media("+573000000001", _at(webhook))
    if expected is None:
        assert isinstance(result, Rejection) and result.error_code == "MEDIA_MATCH_FAILED"
    else:
        assert str(seen[-1].url) == f"{BASE}/messages/{expected}/media"


async def test_the_www_twin_of_the_base_host_is_downloaded() -> None:
    handler, seen = _listing([], b"bytes")
    wazenda, _ = client(handler)
    assert await wazenda.download("https://www.wazenda.com/api/v1/public/media?sig=1") == b"bytes"


async def test_a_listed_media_url_is_fetched_directly() -> None:
    """Verified 31 oct: GET /messages brings a signed media_url on inbound attachments."""
    signed = "https://www.wazenda.com/api/v1/public/media?l=x&m=y&exp=1&sig=ab"
    handler, seen = _listing([_voice(media_url=signed)], b"bytes")
    wazenda, _ = client(handler)

    assert await wazenda.latest_media("+573000000001", _webhook_time()) == b"bytes"
    assert str(seen[-1].url) == signed


async def test_a_listed_media_url_off_wazenda_falls_back_to_the_uuid() -> None:
    handler, seen = _listing([_voice(media_url="https://evil.example/m?sig=1")], b"bytes")
    wazenda, _ = client(handler)

    assert await wazenda.latest_media("+573000000001", _webhook_time()) == b"bytes"
    assert str(seen[-1].url) == f"{BASE}/messages/{VOICE_ID}/media"
