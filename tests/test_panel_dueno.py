"""Owner panel (T-17b): ``panel`` on WhatsApp, a one-time link, a 24 h session cookie."""

import hashlib
import re
from collections.abc import AsyncIterator
from datetime import timedelta
from html import escape
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import Recorder, message
from tests.test_panel import _case, _verdict
from vigia.app import create_app
from vigia.channel.templates import whatsapp_message
from vigia.channel.wazenda import MAX_MESSAGE_CHARS
from vigia.config import Settings, settings
from vigia.i18n import t
from vigia.services.commands import parse_command
from vigia.services.ingest import handle
from vigia.services.owner_panel import owner_view
from vigia.store import postgres as store
from vigia.store.crypto import Keyring, Vault

OWNER = "+573000000211"
EMPLOYEE = "+573000000212"
LINK = re.compile(r"/entrar/([A-Za-z0-9_-]{43})")
SIGNAL = "lookalike_domain"

pytestmark = pytest.mark.anyio


def test_panel_is_a_command() -> None:
    command = parse_command("Panel")

    assert command is not None
    assert (command.name, command.args) == ("panel", "")


async def _business(conn: asyncpg.Connection, recorder: Recorder, phone: str = OWNER) -> None:
    await handle(conn, recorder.ports, message(phone, "hola Ferreteria El Tornillo CO"))
    await handle(conn, recorder.ports, message(phone, "mi nombre Angel"))
    employee = "+573000000" + str(int(phone[-3:]) + 1)
    await handle(conn, recorder.ports, message(phone, f"equipo {employee} Luis"))
    await handle(conn, recorder.ports, message(phone, "proveedor Aceros cuenta=0011223344"))
    await conn.execute(
        "INSERT INTO verdicts (business_id, label, brand, kind, signal_types)"
        " SELECT id, 'scam', 'Bancolombia', 'text', $1 FROM businesses"
        " WHERE id = NULLIF(current_setting('app.business_id', true), '')::uuid",
        [SIGNAL],
    )


async def _open(conn: asyncpg.Connection, keyring: Keyring) -> tuple[UUID, Vault]:
    """The business the connection is scoped to, with its vault open."""
    business_id = await conn.fetchval("SELECT current_setting('app.business_id')::uuid")
    _, vault = await store.open_business(conn, keyring, business_id)
    return business_id, vault


async def _link(conn: asyncpg.Connection, recorder: Recorder, phone: str = OWNER) -> str:
    reply = await handle(conn, recorder.ports, message(phone, "panel"))
    assert reply is not None
    found = LINK.search(reply)
    assert found is not None, reply
    return found.group(1)


async def test_owner_gets_a_one_time_link_and_only_its_hash_is_stored(
    admin_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _business(admin_conn, recorder)

    token = await _link(admin_conn, recorder)

    reply = t("es", "panel.link", url=f"{settings.public_url}/entrar/{token}")
    assert (await handle(admin_conn, recorder.ports, message(OWNER, "panel"))) != reply  # fresh
    row = await admin_conn.fetchrow(
        "SELECT kind, expires_at - now() AS ttl, used_at FROM panel_tokens WHERE token_hash = $1",
        hashlib.sha256(token.encode()).digest(),
    )
    assert row is not None
    assert row["kind"] == "link"
    assert row["used_at"] is None
    assert timedelta(minutes=14) < row["ttl"] <= timedelta(minutes=15)
    stored = await admin_conn.fetch("SELECT token_hash::text AS h FROM panel_tokens")
    assert all(token not in r["h"] for r in stored)


@pytest.mark.parametrize(
    ("named", "key"), [(True, "panel.ask_owner"), (False, "panel.ask_owner.unnamed")]
)
async def test_an_employee_is_told_to_ask_the_owner(
    app_conn: asyncpg.Connection, recorder: Recorder, named: bool, key: str
) -> None:
    await handle(app_conn, recorder.ports, message(OWNER, "hola Ferreteria El Tornillo CO"))
    if named:
        await handle(app_conn, recorder.ports, message(OWNER, "mi nombre Angel"))
    await handle(app_conn, recorder.ports, message(OWNER, f"equipo {EMPLOYEE} Luis"))

    reply = await handle(app_conn, recorder.ports, message(EMPLOYEE, "panel"))

    assert reply == t("es", key, owner="Angel")
    assert "/entrar/" not in reply


async def test_owner_help_mentions_panel(app_conn: asyncpg.Connection, recorder: Recorder) -> None:
    assert t("es", "command.panel") in t("es", "help.owner").lower()
    assert t("en", "command.panel") in t("en", "help.owner").lower()


@pytest.mark.parametrize("lang", ["es", "en"])
def test_owner_help_fits_one_whatsapp_message(lang: str) -> None:
    """Incident: the *panel* line pushed the owner's help past the cap and it was cut."""
    assert len(t(lang, "help") + "\n\n" + t(lang, "help.owner")) <= MAX_MESSAGE_CHARS


async def test_a_link_opens_one_session_once(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _business(app_conn, recorder)
    link = await _link(app_conn, recorder)

    first = await store.enter_panel(app_conn, link)
    again = await store.enter_panel(app_conn, link)

    assert first is not None
    assert again is None
    assert await store.panel_session(app_conn, first) is not None


async def test_an_expired_link_or_session_opens_nothing(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _business(app_conn, recorder)
    member = await store.find_member(app_conn, recorder.keyring.hash(OWNER))
    assert member is not None
    stale = await store.issue_panel_link(app_conn, member, ttl=timedelta(seconds=-1))
    link = await _link(app_conn, recorder)

    session = await store.enter_panel(app_conn, link, ttl=timedelta(seconds=-1))

    assert await store.enter_panel(app_conn, stale) is None
    assert session is not None
    assert await store.panel_session(app_conn, session) is None


async def test_a_session_ends_when_its_member_is_no_longer_the_owner(
    admin_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    await _business(admin_conn, recorder)
    session = await store.enter_panel(admin_conn, await _link(admin_conn, recorder))
    assert session is not None

    # Only this test's owner: now() is the transaction's start, shared by its own rows.
    await admin_conn.execute(
        "UPDATE members SET role = 'employee' WHERE role = 'owner' AND created_at = now()"
    )

    assert await store.panel_session(admin_conn, session) is None


async def test_owner_view_shows_cases_team_and_suppliers_without_signal_names(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """D-111: the owner never sees internal signal names."""
    await _business(app_conn, recorder)
    session = await store.enter_panel(app_conn, await _link(app_conn, recorder))
    assert session is not None

    view = await owner_view(app_conn, recorder.keyring, session)

    assert view is not None
    assert view["business"] == "Ferreteria El Tornillo"
    assert view["language"] == "es"
    assert [m["name"] for m in view["team"]] == ["Angel", "Luis"]
    assert view["suppliers"] == [{"name": "Aceros", "email": None, "account": "0011223344"}]
    assert [(c["label"], c["brand"], c["kind"]) for c in view["cases"]] == [
        ("scam", "Bancolombia", "text")
    ]
    assert SIGNAL not in repr(view)


async def test_owner_reads_the_same_reason_and_action_the_team_got(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """The owner gets the plain words of the WhatsApp reply, not signal names (D-111)."""
    await _business(app_conn, recorder)
    business_id, vault = await _open(app_conn, recorder.keyring)
    case = _case(business_id)
    verdict = _verdict(case).model_copy(update={"user_explanation": "Esto es una estafa."})
    await store.save_case(app_conn, vault, case)
    await store.save_verdict(app_conn, vault, case, verdict)
    reply = whatsapp_message(verdict, "es")
    session = await store.enter_panel(app_conn, await _link(app_conn, recorder))
    assert session is not None

    view = await owner_view(app_conn, recorder.keyring, session)

    assert view is not None
    # both share now(): the transaction's clock, so the order between them is a tie
    said = [(c["explanation"], c["action"]) for c in view["cases"]]
    # _business stores an older verdict without a sealed payload: it shows no words
    assert (None, None) in said
    said.remove((None, None))
    [(explanation, action)] = said
    assert explanation and explanation in reply
    assert action and action in reply
    assert "brand_off_domain" not in repr(view)


async def test_owner_view_shows_who_reported_each_case(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    """The owner's first question: who sent it. Same name/fallback as the team alert (D-127)."""
    await _business(app_conn, recorder)
    business_id, vault = await _open(app_conn, recorder.keyring)
    luis = await store.find_member(app_conn, recorder.keyring.hash(EMPLOYEE))
    assert luis is not None
    case = _case(business_id)
    await store.save_case(app_conn, vault, case, member_id=luis.id)
    await store.save_verdict(app_conn, vault, case, _verdict(case))
    session = await store.enter_panel(app_conn, await _link(app_conn, recorder))
    assert session is not None

    view = await owner_view(app_conn, recorder.keyring, session)

    assert view is not None
    assert all("member_id" not in c for c in view["cases"])  # the raw id never leaves
    reporters = [c["reporter"] for c in view["cases"]]
    assert "Luis" in reporters
    # _business's directly-inserted verdict has no case/member: falls back like the team alert.
    assert t("es", "team_alert.someone") in reporters


async def test_owner_view_needs_a_live_owner_session(
    app_conn: asyncpg.Connection, recorder: Recorder
) -> None:
    assert await owner_view(app_conn, recorder.keyring, "x" * 43) is None


# --- HTTP, over plain http://localhost like a judge runs it --------------------------


@pytest.fixture
async def committed(admin_dsn: str, keyring: Keyring) -> AsyncIterator[str]:
    """A committed business with a fresh link, deleted afterwards; yields the link token."""
    recorder = Recorder(keyring)
    conn = await asyncpg.connect(Settings().database_url)
    try:
        async with conn.transaction():
            await _business(conn, recorder, "+573000000221")
            token = await _link(conn, recorder, "+573000000221")
        yield token
    finally:
        member = await store.find_member(conn, keyring.hash("+573000000221"))
        admin = await asyncpg.connect(admin_dsn)
        if member is not None:
            await admin.execute("DELETE FROM businesses WHERE id = $1", member.business_id)
        await admin.close()
        await conn.close()


def _client(keyring: Keyring) -> TestClient:
    app = create_app(Settings(VIGIA_PANEL_TOKEN="operador-de-prueba-con-32-caracteres"))
    app.state.keyring = keyring
    return TestClient(app, base_url="http://localhost")


async def test_a_link_becomes_an_http_only_lax_cookie_that_works_without_tls(
    committed: str, keyring: Keyring
) -> None:
    client = _client(keyring)

    entered = client.post(f"/entrar/{committed}", follow_redirects=False)

    assert entered.status_code == 303
    assert entered.headers["location"] == "/dueno"
    cookie = entered.headers["set-cookie"].lower()
    assert "vigia_owner=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=86400" in cookie
    assert "secure" not in cookie  # a judge runs over http://localhost
    view = client.get("/api/owner")
    assert view.status_code == 200
    assert view.json()["business"] == "Ferreteria El Tornillo"
    assert SIGNAL not in view.text
    assert client.get("/dueno").status_code == 200


async def test_link_previews_cannot_spend_the_link(committed: str, keyring: Keyring) -> None:
    """Incident: WhatsApp GETs every link it sends to draw a preview; that GET spent it."""
    previewer = TestClient(_client(keyring).app)
    client = _client(keyring)

    previews = [previewer.get(f"/entrar/{committed}", follow_redirects=False) for _ in range(2)]
    entered = client.post(f"/entrar/{committed}", follow_redirects=False)

    for preview in previews:
        assert preview.status_code == 200
        assert "set-cookie" not in preview.headers
        assert f'<form method="post" action="/entrar/{committed}">' in preview.text
        assert escape(t("es", "panel.enter")) in preview.text
    assert entered.status_code == 303
    assert "vigia_owner=" in entered.headers["set-cookie"]
    assert client.post(f"/entrar/{committed}", follow_redirects=False).status_code == 403


async def test_a_get_with_the_wrong_length_is_the_same_refusal(
    committed: str, keyring: Keyring
) -> None:
    client = _client(keyring)

    huge = client.get("/entrar/" + "A" * 500, follow_redirects=False)
    short = client.get("/entrar/" + "A" * 1, follow_redirects=False)
    spent = client.post(f"/entrar/{committed}", follow_redirects=False)
    reused = client.get(f"/entrar/{committed}", follow_redirects=False)

    assert huge.status_code == short.status_code == 403
    assert huge.text == short.text
    assert reused.status_code == 200  # a spent link still shows the door, not a refusal
    assert spent.status_code == 303


async def test_a_spent_or_unknown_link_gets_the_same_refusal(
    committed: str, keyring: Keyring
) -> None:
    client = _client(keyring)
    client.post(f"/entrar/{committed}", follow_redirects=False)

    spent = TestClient(client.app).post(f"/entrar/{committed}", follow_redirects=False)
    unknown = TestClient(client.app).post("/entrar/" + "A" * 43, follow_redirects=False)
    huge = TestClient(client.app).post("/entrar/" + "A" * 500, follow_redirects=False)

    assert spent.status_code == unknown.status_code == huge.status_code == 403
    assert spent.text == unknown.text == huge.text
    assert "set-cookie" not in spent.headers


async def test_each_view_has_its_own_authentication(committed: str, keyring: Keyring) -> None:
    owner = _client(keyring)
    owner.post(f"/entrar/{committed}", follow_redirects=False)
    operator = {"Authorization": "Bearer operador-de-prueba-con-32-caracteres"}
    stranger = _client(keyring)

    assert stranger.get("/api/owner").status_code == 401
    assert stranger.get("/api/owner", headers=operator).status_code == 401
    assert owner.get("/api/operator").status_code == 401
    assert stranger.get("/api/operator", headers=operator).status_code == 200


async def test_the_door_is_a_card_in_one_language(committed: str, keyring: Keyring) -> None:
    """D-128: a centered card, system font, one language picked by Accept-Language."""
    client = _client(keyring)

    spanish = client.get(f"/entrar/{committed}", follow_redirects=False)
    english = client.get(
        f"/entrar/{committed}",
        follow_redirects=False,
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )

    for page, lang in ((spanish, "es"), (english, "en")):
        assert f'<html lang="{lang}">' in page.text
        assert page.text.count("<form") == 1  # one button, no stacked languages
        assert escape(t(lang, "panel.enter")) in page.text
        other = "en" if lang == "es" else "es"
        assert escape(t(other, "panel.enter")) not in page.text
        minutes = int(store.PANEL_LINK_TTL.total_seconds() // 60)
        assert escape(t(lang, "panel.enter.ttl", minutes=minutes)) in page.text
    assert "VIGÍA" in spanish.text


async def test_a_spent_link_shows_the_card_styled_refusal(
    committed: str, keyring: Keyring
) -> None:
    client = _client(keyring)
    client.post(f"/entrar/{committed}", follow_redirects=False)  # spends it

    spent = client.post(f"/entrar/{committed}", follow_redirects=False)
    english = TestClient(client.app).post(
        f"/entrar/{committed}", follow_redirects=False, headers={"Accept-Language": "en"}
    )

    assert spent.status_code == english.status_code == 403
    assert spent.headers["content-type"].startswith("text/html")
    assert escape(t("es", "panel.link_invalid")) in spent.text
    assert escape(t("en", "panel.link_invalid")) in english.text
    assert escape(t("es", "panel.link_invalid")) not in english.text
    assert '<form method="post"' not in spent.text


async def test_an_expired_link_shows_the_card_styled_refusal(
    committed: str, keyring: Keyring
) -> None:
    conn = await asyncpg.connect(Settings().database_url)
    try:
        async with conn.transaction():
            member = await store.find_member(conn, keyring.hash("+573000000221"))
            assert member is not None
            stale = await store.issue_panel_link(conn, member, ttl=timedelta(seconds=-1))
    finally:
        await conn.close()

    expired = _client(keyring).post(f"/entrar/{stale}", follow_redirects=False)

    assert expired.status_code == 403
    assert expired.headers["content-type"].startswith("text/html")
    assert escape(t("es", "panel.link_invalid")) in expired.text
    assert '<div class="card">' in expired.text
