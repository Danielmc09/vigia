"""T-02: verify Meta Model API endpoints with minimal real calls and save raw fixtures.

Run with ``uv run --env-file .env python herramientas/verificar_meta.py``.
The key is read only from ``os.environ['MODEL_API_KEY']``; it is never printed or saved.
Inputs are synthetic (generated shapes, PDF and macOS ``say`` speech). Budget cap: 1 USD.
"""

import base64
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import wave
import zlib
from pathlib import Path

import httpx

BASE = "https://api.meta.ai/v1"
SPARK = "muse-spark-1.3"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "meta"
BUDGET_USD = 1.0
# USD per 1M tokens, Standard tier (dev.meta.ai/docs/pricing-rate-limits, 2026-10-27)
PRICE_IN, PRICE_CACHED, PRICE_OUT = 1.25, 0.15, 4.25
PRICE_SEARCH, PRICE_SAM_IMAGE, PRICE_ASR_HOUR = 2.5 / 1000, 2.5 / 1000, 0.18

KEY = os.environ.get("MODEL_API_KEY", "")
spent = 0.0
summary: list[dict] = []


def png_square() -> bytes:
    """Build a 64x64 white PNG with a centred red square (stdlib only)."""
    size = 64
    rows = b""
    for y in range(size):
        row = b"\x00"
        for x in range(size):
            red = 16 <= x < 48 and 16 <= y < 48
            row += b"\xff\x00\x00" if red else b"\xff\xff\xff"
        rows += row

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def pdf_one_line(text: str) -> bytes:
    """Build a one-page PDF containing ``text`` with correct xref offsets."""
    stream = f"BT /F1 14 Tf 40 760 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def wav_speech(text: str) -> bytes:
    """Synthesize ``text`` with macOS ``say`` as WAV, mono, 16-bit PCM, 16 kHz."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.wav"
        subprocess.run(  # noqa: S603 - fixed argv, synthetic text
            ["/usr/bin/say", "-v", "Paulina", "-o", str(path), "--data-format=LEI16@16000", text],
            check=True,
        )
        with wave.open(str(path)) as src:
            frames = src.readframes(src.getnframes())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as dst:  # canonical RIFF: no JUNK/FLLR chunks from `say`
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(16000)
        dst.writeframes(frames)
    return buf.getvalue()


def redact(obj: object) -> object:
    """Replace long base64/data-URI strings so fixtures stay small and readable."""
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 300:
        return f"<{len(obj)} chars elided: {obj[:40]}...>"
    return obj


def usd_from_usage(usage: dict | None) -> tuple[float, dict]:
    """Estimate USD for a Spark call from its usage block (chat or responses naming)."""
    if not usage:
        return 0.0, {}
    tin = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    tout = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0) or 0
    usd = ((tin - cached) * PRICE_IN + cached * PRICE_CACHED + tout * PRICE_OUT) / 1e6
    return usd, {"input": tin, "cached": cached, "output": tout}


def save(name: str, record: dict) -> None:
    """Write a fixture after asserting it carries no key or auth header."""
    text = json.dumps(record, ensure_ascii=False, indent=2)
    low = text.lower()
    if (KEY and KEY in text) or "bearer " in low or '"authorization"' in low:
        raise RuntimeError(f"fixture {name} would leak credentials; not written")
    (OUT / f"{name}.json").write_text(text + "\n")


def call(name: str, method: str, path: str, extra_usd: float = 0.0, **kw: object) -> dict | None:
    """Make one call, record status/latency/cost, save the raw fixture, return parsed JSON."""
    global spent
    if spent >= BUDGET_USD:
        print(f"[{name}] skipped: budget {BUDGET_USD} USD reached")
        return None
    body = kw.get("json")
    t0 = time.perf_counter()
    try:
        r = httpx.request(
            method, BASE + path, headers={"Authorization": f"Bearer {KEY}"}, timeout=120, **kw
        )
    except httpx.HTTPError as exc:
        summary.append({"name": name, "status": "error", "error": type(exc).__name__})
        print(f"[{name}] transport error: {type(exc).__name__}")
        return None
    ms = round((time.perf_counter() - t0) * 1000)
    try:
        parsed = r.json()
    except ValueError:
        parsed = None
    usage = parsed.get("usage") if isinstance(parsed, dict) else None
    usd, tokens = usd_from_usage(usage)
    usd += extra_usd if r.is_success else 0.0
    spent += usd
    keep = {"content-type", "x-request-id", "request-id"}
    keep |= {h for h in r.headers if h.lower().startswith("x-ratelimit")}
    record = {
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": {"method": method, "url": BASE + path, "json": redact(body)},
        "status": r.status_code,
        "latency_ms": ms,
        "tokens": tokens,
        "usd_estimated": round(usd, 6),
        "response_headers": {k: v for k, v in r.headers.items() if k.lower() in keep},
        "response": parsed if parsed is not None else r.text,
    }
    save(name, record)
    summary.append({"name": name, "status": r.status_code, "ms": ms, "usd": round(usd, 6)})
    print(f"[{name}] {r.status_code} {ms} ms tokens={tokens} usd={usd:.6f}")
    return parsed


def chat(name: str, content: object, **extra: object) -> dict | None:
    """Minimal Spark chat completion with one user message."""
    body = {
        "model": SPARK,
        "messages": [{"role": "user", "content": content}],
        "max_completion_tokens": 300,
        "reasoning_effort": "minimal",
        **extra,
    }
    return call(name, "POST", "/chat/completions", json=body)


def data_uri(mime: str, raw: bytes) -> str:
    """Encode bytes as a base64 data URI."""
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def main() -> int:
    """Run every verification call and print a summary; exit 1 only if setup fails."""
    if not KEY:
        print("MODEL_API_KEY missing; run with `uv run --env-file .env ...`")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    png, pdf = png_square(), pdf_one_line("Factura 0042. Pague hoy en pagos-ejemplo.test")
    wav = wav_speech("Hola, le habla el banco. Su cuenta fue bloqueada, llame ya.")

    if call("models", "GET", "/models") is None:
        return 1
    verdict_schema = {
        "type": "object",
        "properties": {
            "veredicto": {"type": "string", "enum": ["estafa", "legitimo", "dudoso"]},
            "razon": {"type": "string"},
        },
        "required": ["veredicto", "razon"],
        "additionalProperties": False,
    }
    chat(
        "chat_json_schema",
        "Clasifica: 'Su paquete está retenido, pague 2 USD en pagos-ejemplo.test'.",
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "Veredicto", "schema": verdict_schema, "strict": True},
        },
    )
    chat(
        "chat_tools",
        "Analiza la URL https://pagos-ejemplo.test/login usando la herramienta.",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "analizar_url",
                    "description": "Return deterministic signals for a URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            }
        ],
    )
    chat(
        "chat_image",
        [
            {"type": "text", "text": "¿De qué color es el cuadrado? Una palabra."},
            {"type": "image_url", "image_url": {"url": data_uri("image/png", png)}},
        ],
    )
    chat(
        "chat_pdf",
        [
            {"type": "text", "text": "Copia literal el texto del PDF."},
            {
                "type": "file",
                "file": {"filename": "f.pdf", "file_data": data_uri("application/pdf", pdf)},
            },
        ],
    )
    chat(
        "chat_audio",
        [
            {"type": "text", "text": "Transcribe el audio literalmente."},
            {
                "type": "input_audio",
                "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"},
            },
        ],
    )
    sam(png)
    asr(wav)
    call(
        "responses_web_search",
        "POST",
        "/responses",
        extra_usd=PRICE_SEARCH,
        json={
            "model": SPARK,
            "input": "¿Cuál es el dominio oficial de la DIAN (Colombia)? Solo el dominio.",
            "tools": [{"type": "web_search", "search_context_size": "low"}],
            "reasoning": {"effort": "minimal"},
            "max_output_tokens": 200,
        },
    )
    chat(
        "chat_web_search",
        "¿Cuál es el dominio oficial de la DIAN? Solo el dominio.",
        tools=[{"type": "web_search"}],
    )
    print(json.dumps({"calls": summary, "usd_total": round(spent, 6)}, indent=2))
    if spent > BUDGET_USD:
        print("BUDGET EXCEEDED")
    return 0


def sam(png: bytes) -> None:
    """SAM 3.1 via the Responses API (streamed); saves the raw SSE text."""
    body = {
        "model": "sam-3.1",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "red square"},
                    {"type": "input_image", "image_url": data_uri("image/png", png)},
                ],
            }
        ],
        "stream": True,
    }
    stream_call("sam_segment", body, PRICE_SAM_IMAGE)


def stream_call(name: str, body: dict, flat_usd: float) -> None:
    """POST a streamed Responses request and store the raw SSE body."""
    global spent
    t0 = time.perf_counter()
    r = httpx.post(
        BASE + "/responses", headers={"Authorization": f"Bearer {KEY}"}, json=body, timeout=120
    )
    ms = round((time.perf_counter() - t0) * 1000)
    usd = flat_usd if r.is_success else 0.0
    spent += usd
    save(
        name,
        {
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "request": {"method": "POST", "url": BASE + "/responses", "json": redact(body)},
            "status": r.status_code,
            "latency_ms": ms,
            "usd_estimated": usd,
            "response_headers": {"content-type": r.headers.get("content-type")},
            "response_raw": r.text,
        },
    )
    summary.append({"name": name, "status": r.status_code, "ms": ms, "usd": usd})
    print(f"[{name}] {r.status_code} {ms} ms usd={usd:.6f}")


def asr(wav: bytes) -> None:
    """Muse Voice Transcribe batch endpoint (multipart: request JSON + WAV)."""
    seconds = (len(wav) - 44) / (16000 * 2)
    req = {"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV", "languageBias": ["es"]}
    call(
        "asr_transcribe",
        "POST",
        f"/asr/transcribe?sessionId={uuid.uuid4()}",
        extra_usd=int(seconds) / 3600 * PRICE_ASR_HOUR,
        files={
            "request": (None, json.dumps(req), "application/json"),
            "audio": ("a.wav", wav, "audio/wav"),
        },
    )


if __name__ == "__main__":
    sys.exit(main())
