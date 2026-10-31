"""Probe Wazenda's message listing and media download (T-10e), saving anonymized fixtures.

Run with ``uv run --env-file .env python herramientas/verificar_wazenda.py``. The key is read
from the environment and never printed; every phone, id and free text is replaced before
anything is written or shown (rules 10 and 19).
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

OUT = Path("tests/fixtures/wazenda")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_PHONE = re.compile(r"^\+?\d{10,15}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})?$")
_TOKEN = re.compile(r"^[a-z_/.-]{1,24}$")


class _Anon:
    """Stable replacements, so relations between fields survive anonymization."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def _swap(self, value: str, template: str) -> str:
        if value not in self.seen:
            self.seen[value] = template.format(n=len(self.seen) + 1)
        return self.seen[value]

    def uuid(self, value: str) -> str:
        return self._swap(value.lower(), "00000000-0000-4000-8000-{n:012d}")

    def value(self, key: str, value: object) -> object:
        if isinstance(value, dict):
            return {k: self.value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(key, v) for v in value]
        if not isinstance(value, str):
            return value
        if _UUID.fullmatch(value):
            return self.uuid(value)
        if _PHONE.fullmatch(value):
            return self._swap(value, value[: value.startswith("+")] + "57300000{n:04d}")
        if value.startswith("wamid."):
            return self._swap(value, "wamid.SYNTHETIC{n:04d}")
        if value.startswith("http"):
            parts = urlsplit(value)
            path = _UUID.sub(lambda m: self.uuid(m.group()), parts.path)
            return f"{parts.scheme}://{parts.hostname}{path}" + ("?<signed>" if parts.query else "")
        if _ISO.match(value) or _TOKEN.match(value) or re.fullmatch(r"\[\w+\]", value):
            return value
        return "<redacted>"


def main() -> int:
    """Call the listing and one media download; write fixtures; print only the shape."""
    base, key, line = (
        os.environ.get(k) for k in ("WAZENDA_BASE_URL", "WAZENDA_API_KEY", "WAZENDA_LINE_ID")
    )
    if not (base and key and line):
        print("WAZENDA_* missing; run with `uv run --env-file .env ...`")
        return 1
    anon = _Anon()
    headers = {"X-API-Key": key}
    with httpx.Client(timeout=30) as http:
        start = time.monotonic()
        listing = http.get(
            f"{base}/messages", params={"line_id": line, "limit": 10}, headers=headers
        )
        latency = round(time.monotonic() - start, 2)
        body = listing.json()
        saved = {
            "request": {
                "method": "GET",
                "path": "/messages",
                "params": {"line_id": anon.uuid(line), "limit": 10},
            },
            "status": listing.status_code,
            "latency_s": latency,
            "response": anon.value("", body),
        }
        (OUT / "listado.json").write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
        print("listing", listing.status_code, latency, "s")
        items = (
            body
            if isinstance(body, list)
            else next((v for v in body.values() if isinstance(v, list)), [])
        )
        print(
            "top-level:", type(body).__name__, list(body) if isinstance(body, dict) else len(body)
        )
        for item in items:
            print({k: (type(v).__name__, anon.value(k, v)) for k, v in item.items()})
        media = next((m for m in items if m.get("media_type")), None)
        if media is None:
            print("no item with media_type")
            return 0
        start = time.monotonic()
        got = http.get(f"{base}/messages/{media['id']}/media", headers=headers)
        is_json = got.headers.get("content-type", "").startswith("application/json")
        failed_json = got.status_code != 200 and is_json
        meta = {
            "request": {"method": "GET", "path": f"/messages/{anon.uuid(media['id'])}/media"},
            "status": got.status_code,
            "latency_s": round(time.monotonic() - start, 2),
            "content_type": got.headers.get("content-type"),
            "bytes": len(got.content),
            "magic_hex": got.content[:12].hex() if got.status_code == 200 else None,
            "error_body": anon.value("", got.json()) if failed_json else None,
        }
        (OUT / "media_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        print("media", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
