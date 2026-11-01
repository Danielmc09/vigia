"""The React panel beside the server-rendered one: its JSON API, its shell and its bundle."""

import hashlib
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_panel import TOKEN, _client
from vigia.app import MAX_CASE_CHARS
from vigia.channel.panel import APP_JS
from vigia.i18n import t

ROOT = Path(__file__).parents[1]
BEARER = {"Authorization": f"Bearer {TOKEN}"}
SIGNAL = "brand_off_domain"


def test_operator_api_shows_rows_with_signal_names_today_and_rules_only() -> None:
    """D-111: the operator may see signal names; the owner never (tests/test_panel_dueno.py)."""
    response = _client(TOKEN).get("/api/operator", headers=BEARER)

    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["signal_types"] == [SIGNAL]
    assert body["today"] == {"scam": 1}
    assert body["rules_only"] is True
    assert "metrics" in body
    assert response.headers["cache-control"] == "no-store"


def test_operator_api_needs_the_operator_token() -> None:
    assert _client(None).get("/api/operator", headers=BEARER).status_code == 404
    assert _client(TOKEN).get("/api/operator").status_code == 401
    assert _client(TOKEN).post("/api/operator/try", data={"text": "x"}).status_code == 401


def test_operator_try_returns_the_verdict_row_as_json() -> None:
    client = _client(TOKEN)

    response = client.post(
        "/api/operator/try", data={"text": "Ingresa a bancolombia-seguro.com ya"}, headers=BEARER
    )

    assert response.status_code == 200
    tried = response.json()
    assert SIGNAL in tried["row"]["signal_types"]
    assert tried["text"] == "Ingresa a bancolombia-seguro.com ya"
    assert tried["rules_only"] is True


def test_operator_try_refuses_empty_and_too_long_like_the_form() -> None:
    client = _client(TOKEN)

    empty = client.post("/api/operator/try", data={"text": "  "}, headers=BEARER)
    long = client.post(
        "/api/operator/try", data={"text": "a" * (MAX_CASE_CHARS + 1)}, headers=BEARER
    )

    assert (empty.status_code, long.status_code) == (422, 422)


def test_the_react_operator_shell_sits_beside_the_panel_behind_the_same_token() -> None:
    client = _client(TOKEN)

    refused = client.get("/app")
    entered = client.get(f"/app?token={TOKEN}", follow_redirects=False)
    shell = client.get("/app")

    assert refused.status_code == 401
    assert (entered.status_code, entered.headers["location"]) == (303, "/app")
    assert shell.status_code == 200
    assert '<script src="/app.js"' in shell.text
    assert "script-src 'self'" in shell.headers["content-security-policy"]
    assert '<script src="/app.js"' in client.get("/panel").text  # /panel is the shell too


def test_the_shell_embeds_its_strings_as_data_not_code() -> None:
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    page = client.get("/app").text

    found = re.search(r'<script type="application/json" id="strings">(.*?)</script>', page, re.S)
    assert found is not None
    strings = json.loads(found.group(1))
    assert strings["panel.try.title"] == "Try it"
    assert strings["verdict.label.scam"] == "scam"
    assert "<script>" not in page  # the CSP runs no inline script; the shell needs none


def test_the_shell_shows_a_readable_message_if_the_bundle_never_mounts() -> None:
    """A blank page is the worst first impression for a judge; React clears #root on mount."""
    client = _client(TOKEN)
    client.cookies.set("vigia_panel", TOKEN)

    page = client.get("/app").text

    root = re.search(r'<div id="root"[^>]*>(.*?)</div>', page, re.S)
    assert root is not None
    message = t("en", "panel.shell_error")
    assert message in root.group(1)
    assert f"<noscript>{message}</noscript>" in page


def test_the_bundle_is_served_same_origin() -> None:
    response = TestClient(_client(TOKEN).app).get("/app.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.text == APP_JS


def test_the_committed_bundle_is_built_from_the_current_sources() -> None:
    """`make bundle` rebuilds it; a stale bundle means the judge sees old code."""
    sources = sorted((ROOT / "web" / "src").rglob("*.js*"))
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest()

    assert APP_JS.startswith(f"/* src-sha256: {digest} */"), "run `make bundle`"
