"""T-10: POST /webhook/wazenda (specs/01) and the WhatsApp pipeline behind it."""

import asyncio
import io
import json
import logging
import re
import time
import zlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import ValidationError

from tests.conftest import Recorder, message
from tests.test_wazenda import BASE, LINE, SECRET, fixture, opus, sign
from vigia.app import create_app
from vigia.channel.wazenda import Wazenda
from vigia.config import WEBHOOK_CONCURRENCY, Settings
from vigia.domain.attachment import MAX_ATTACHMENT_BYTES, check_wav
from vigia.domain.case import IncomingMessage
from vigia.domain.knowledge import KnownCampaign
from vigia.domain.reading import Transcriber, Transcript
from vigia.domain.rejection import Rejection
from vigia.i18n import t
from vigia.model.jev import make_dev_judge
from vigia.services import whatsapp
from vigia.services.ingest import handle
from vigia.signals.knowledge import confirmed_campaign
from vigia.store import knowledge as store_knowledge
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

OWNER = "+573000000701"
EMPLOYEE = "+573000000702"
pytestmark = pytest.mark.anyio


# --- endpoint (specs/01 steps 1-8) ----------------------------------------------------


class FakePorts:
    def __init__(self, process: Callable[[IncomingMessage], Any] | None = None) -> None:
        self.seen: set[str] = set()
        self.processed: list[IncomingMessage] = []
        self._process = process

    async def first_time(self, message_id: str) -> bool:
        new = message_id not in self.seen
        self.seen.add(message_id)
        return new

    async def process(self, msg: IncomingMessage) -> None:
        self.processed.append(msg)
        if self._process:
            await self._process(msg)

    async def close(self) -> None:
        self.closed = True

    closed = False

    @property
    def ports(self) -> whatsapp.WebhookPorts:
        return whatsapp.WebhookPorts(
            first_time=self.first_time, process=self.process, close=self.close
        )


WAZENDA = {"WAZENDA_API_KEY": "key-not-real", "WAZENDA_LINE_ID": LINE}


def webhook_app(fake: FakePorts, secret: str | None = SECRET.decode()) -> FastAPI:
    config = Settings(WAZENDA_WEBHOOK_SECRET=secret, **WAZENDA) if secret else Settings()
    api = create_app(config)
    api.state.wazenda = fake.ports
    return api


def fresh(name: str, **fields: Any) -> bytes:
    """A fixture with the timestamp set to now, as Wazenda would send it."""
    raw = fixture(name) | {"timestamp": datetime.now(UTC).isoformat()} | fields
    return json.dumps(raw).encode()


async def post(api: FastAPI, body: bytes, signature: str | None) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Wazenda-Signature"] = signature
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/webhook/wazenda", content=body, headers=headers)


async def drain(api: FastAPI) -> None:
    await asyncio.gather(*list(getattr(api.state, "webhook_tasks", ())))


async def test_a_signed_message_is_accepted_and_processed_in_background() -> None:
    fake = FakePorts()
    api = webhook_app(fake)
    body = fresh("texto")

    response = await post(api, body, sign(body))
    await drain(api)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert [m.sender for m in fake.processed] == ["+573000000001"]


async def test_the_ack_does_not_wait_for_processing() -> None:
    never = asyncio.Event()

    async def slow(_: IncomingMessage) -> None:
        await never.wait()

    fake = FakePorts(slow)
    api = webhook_app(fake)
    body = fresh("texto")

    started = time.monotonic()
    response = await post(api, body, sign(body))

    assert response.status_code == 202
    assert time.monotonic() - started < 1
    for task in list(api.state.webhook_tasks):
        task.cancel()


@pytest.mark.parametrize("signature", [None, "sha256=" + "0" * 64, "garbage"])
async def test_a_bad_signature_is_401_and_nothing_runs(signature: str | None) -> None:
    fake = FakePorts()
    api = webhook_app(fake)

    response = await post(api, fresh("texto"), signature)

    assert response.status_code == 401
    assert fake.processed == [] and fake.seen == set()


async def test_without_a_configured_secret_every_request_is_401() -> None:
    fake = FakePorts()
    api = webhook_app(fake, secret=None)
    body = fresh("texto")

    assert (await post(api, body, sign(body))).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        json.dumps(fixture("status")).encode(),
        fresh("texto", body=None),
    ],
    ids=["status", "no-body-no-media"],
)
async def test_events_without_a_message_are_ignored_with_200(body: bytes) -> None:
    fake = FakePorts()
    api = webhook_app(fake)

    response = await post(api, body, sign(body))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert fake.processed == []


async def test_a_stale_message_is_ignored_and_logged_without_pii(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakePorts()
    api = webhook_app(fake)
    body = fresh("texto", timestamp="2020-01-01T00:00:00Z")

    with caplog.at_level(logging.INFO):
        response = await post(api, body, sign(body))

    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    assert fake.processed == []
    assert "webhook_stale" in caplog.text
    assert "***0001" in caplog.text
    assert "+573000000001" not in caplog.text and "Bancolombia" not in caplog.text


async def test_a_repeated_message_id_is_a_200_duplicate() -> None:
    fake = FakePorts()
    api = webhook_app(fake)
    body = fresh("texto")

    first = await post(api, body, sign(body))
    again = await post(api, body, sign(body))
    await drain(api)

    assert first.status_code == 202
    assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert len(fake.processed) == 1


async def test_a_failure_in_background_is_logged_without_pii(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(_: IncomingMessage) -> None:
        raise RuntimeError("+573000000001 Bancolombia")

    api = webhook_app(FakePorts(boom))
    body = fresh("texto")

    with caplog.at_level(logging.INFO):
        await post(api, body, sign(body))
        await drain(api)

    assert "webhook_failed" in caplog.text
    assert "+573000000001" not in caplog.text and "Bancolombia" not in caplog.text


@pytest.mark.parametrize(
    "partial",
    [
        {"WAZENDA_WEBHOOK_SECRET": "s"},
        {"WAZENDA_WEBHOOK_SECRET": "s", "WAZENDA_API_KEY": "k"},
        {"WAZENDA_API_KEY": "k", "WAZENDA_LINE_ID": "l"},
    ],
    ids=["secret-only", "no-line", "no-secret"],
)
def test_wazenda_settings_come_all_together_or_not_at_all(partial: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="WAZENDA_"):
        Settings(**partial)


async def test_shutdown_waits_for_pending_messages_and_closes_the_ports() -> None:
    gate, done = asyncio.Event(), []

    async def slow(msg: IncomingMessage) -> None:
        await gate.wait()
        done.append(msg)

    fake = FakePorts(slow)
    api = webhook_app(fake)
    body = fresh("texto")

    async with api.router.lifespan_context(api):
        assert (await post(api, body, sign(body))).status_code == 202
        asyncio.get_running_loop().call_later(0.05, gate.set)

    assert len(done) == 1 and fake.closed


async def test_in_flight_processing_is_capped() -> None:
    running, peak, gate = 0, 0, asyncio.Event()

    async def slow(_: IncomingMessage) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await gate.wait()
        running -= 1

    fake = FakePorts(slow)
    api = webhook_app(fake)
    for n in range(WEBHOOK_CONCURRENCY + 4):
        body = fresh("texto", message_id=f"wamid.cap{n}")
        assert (await post(api, body, sign(body))).status_code == 202
    await asyncio.sleep(0.05)

    assert peak == WEBHOOK_CONCURRENCY
    gate.set()
    await drain(api)
    assert len(fake.processed) == WEBHOOK_CONCURRENCY + 4


# --- idempotency table (0008) ---------------------------------------------------------


async def test_webhook_events_remember_a_message_id_hash(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    id_hash = keyring.hash("wazenda:wamid.TEST")

    assert await store.first_webhook_event(app_conn, id_hash)
    assert not await store.first_webhook_event(app_conn, id_hash)
    assert await store.first_webhook_event(app_conn, keyring.hash("wazenda:wamid.OTHER"))


async def test_the_app_role_cannot_read_webhook_events(app_conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.fetch("SELECT * FROM webhook_events")


# --- pipeline (services/whatsapp.py) --------------------------------------------------


class Outbox:
    """Fake Wazenda: serves one media file and records what VIGIA sends."""

    def __init__(self, media: bytes = b"", listing: list[dict[str, Any]] | None = None) -> None:
        self.media = media
        self.listing = listing or []
        self.sent: list[tuple[str, str]] = []
        self.templates: list[dict[str, Any]] = []
        self.downloads = 0
        self.templates_fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/messages"):
            return httpx.Response(200, json=self.listing)
        if request.method == "GET":
            self.downloads += 1
            return httpx.Response(200, content=self.media)
        body = json.loads(request.content)
        if request.url.path.endswith("/templates"):
            self.templates.append(body)
            return httpx.Response(500 if self.templates_fail else 200, json={"ok": True})
        self.sent.append((body["number"], body["message"]))
        return httpx.Response(200, json={"ok": True})

    def to(self, number: str) -> list[str]:
        return [text for to, text in self.sent if to == number]


def channel(
    keyring: Keyring,
    outbox: Outbox,
    readers: dict[str, Transcriber] | None = None,
    config: Settings | None = None,
) -> whatsapp.Channel:
    http = httpx.AsyncClient(transport=httpx.MockTransport(outbox))
    return whatsapp.Channel(
        keyring=keyring,
        wazenda=Wazenda(BASE, "key-not-real", LINE, http),
        config=config or Settings(),
        readers=readers or {},
        analyst=lambda suppliers, media: None,
        judge=make_dev_judge(Settings()),
    )


async def team(conn: asyncpg.Connection, recorder: Recorder) -> None:
    await handle(conn, recorder.ports, message(OWNER, "hola Ferreteria El Tornillo CO"))
    await handle(conn, recorder.ports, message(OWNER, "equipo +57 300 000 0702 Luis"))


def incoming(name: str, sender: str, **fields: Any) -> IncomingMessage:
    msg = whatsapp_message(fixture(name) | {"from": sender} | fields)
    return msg


def whatsapp_message(raw: dict[str, Any]) -> IncomingMessage:
    from vigia.channel.wazenda import to_message

    msg = to_message(raw)
    assert msg is not None
    return msg


SCAM_TEXT = fixture("texto")["body"]


async def test_a_scam_gets_a_verdict_a_reply_and_alerts_the_rest_of_the_team(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()

    await whatsapp.process(app_conn, channel(keyring, outbox), incoming("texto", EMPLOYEE))

    [reply] = outbox.to(EMPLOYEE)
    assert reply.startswith("Esto es una estafa")
    assert reply.endswith(t("es", "message.id_line", ref="1"))
    [alert] = outbox.to(OWNER)
    assert "Luis" in alert and "Bancolombia" in alert
    assert len(alert) <= 300  # hook + disarmed indicator (T-10d), still one glance
    assert [r["label"] for r in await app_conn.fetch("SELECT label FROM verdicts")] == ["scam"]


async def test_a_legit_message_alerts_nobody(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    legit = incoming("texto", EMPLOYEE, body="Hola Luis, el pedido de tornillos llega el viernes.")

    await whatsapp.process(app_conn, channel(keyring, outbox), legit)

    assert len(outbox.to(EMPLOYEE)) == 1
    assert outbox.to(OWNER) == []


async def test_a_second_scam_of_the_same_brand_does_not_alert_again(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    ch = channel(keyring, outbox)

    await whatsapp.process(app_conn, ch, incoming("texto", EMPLOYEE))
    await whatsapp.process(app_conn, ch, incoming("texto", EMPLOYEE, message_id="wamid.2"))

    assert len(outbox.to(EMPLOYEE)) == 2
    assert len(outbox.to(OWNER)) == 1  # specs/05: 6 h cooldown per brand


QUIET = "+573000000703"


async def test_t18b_nobody_is_alerted_from_a_number_that_never_wrote(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """D-087: Marta was enrolled by the owner but never wrote; Meta would not deliver."""
    await team(app_conn, recorder)
    await handle(app_conn, recorder.ports, message(OWNER, "equipo +57 300 000 0703 Marta"))
    outbox = Outbox()

    await whatsapp.process(app_conn, channel(keyring, outbox), incoming("texto", EMPLOYEE))

    assert len(outbox.to(OWNER)) == 1
    assert outbox.to(QUIET) == [] and outbox.templates == []


async def _owner_out_of_window(admin_conn: asyncpg.Connection) -> None:
    await admin_conn.execute(
        "UPDATE members SET last_inbound_at = now() - interval '25 hours'"
        " WHERE last_inbound_at IS NOT NULL AND role = 'owner'"
    )


async def test_t18b_out_of_window_without_template_leaves_a_visible_rejection(
    admin_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """Never silence: the panel shows why the owner got no alert."""
    await team(admin_conn, recorder)
    await _owner_out_of_window(admin_conn)
    outbox = Outbox()

    await whatsapp.process(admin_conn, channel(keyring, outbox), incoming("texto", EMPLOYEE))

    assert outbox.to(OWNER) == [] and outbox.templates == []
    newest, *_ = await store.panel_verdicts(admin_conn, 5)  # superuser: RLS does not narrow
    assert newest.alert_rejections == ["TEMPLATE_PENDING"]


async def test_t18b_out_of_window_with_template_sends_the_approved_template(
    admin_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(admin_conn, recorder)
    await _owner_out_of_window(admin_conn)
    outbox = Outbox()
    config = Settings(WAZENDA_ALERT_TEMPLATE="alerta_equipo_estafa")
    ch = channel(keyring, outbox, config=config)

    await whatsapp.process(admin_conn, ch, incoming("texto", EMPLOYEE))

    assert outbox.to(OWNER) == []
    [sent] = outbox.templates
    assert sent["number"] == OWNER and sent["template_name"] == "alerta_equipo_estafa"
    assert sent["language"] == "es"
    assert sent["body_params"][:2] == ["Ferreteria El Tornillo", "Luis"]


async def test_t18b_a_template_wazenda_refused_is_visible_too(
    admin_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """Never silence: a send that fails is a rejection on the verdict, not a log line."""
    await team(admin_conn, recorder)
    await _owner_out_of_window(admin_conn)
    outbox = Outbox()
    outbox.templates_fail = True
    config = Settings(WAZENDA_ALERT_TEMPLATE="alerta_equipo_estafa")

    await whatsapp.process(
        admin_conn, channel(keyring, outbox, config=config), incoming("texto", EMPLOYEE)
    )

    newest, *_ = await store.panel_verdicts(admin_conn, 5)
    assert newest.alert_rejections == ["CHANNEL_UNAVAILABLE"]


async def test_a_command_is_answered_over_whatsapp(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()

    ask = incoming("texto", OWNER, body="ayuda")
    await whatsapp.process(app_conn, channel(keyring, outbox), ask)

    assert outbox.to(OWNER) == [f"{t('es', 'help')}\n\n{t('es', 'help.owner')}"]


async def test_a_stranger_gets_nothing_and_no_media_is_downloaded(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    outbox = Outbox(opus())

    await whatsapp.process(app_conn, channel(keyring, outbox), incoming("audio", "+573000000799"))

    assert outbox.sent == [] and outbox.downloads == 0


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    return buf.getvalue()


class FakeReader:
    def __init__(self, result: Transcript | Rejection | None = None) -> None:
        self.calls: list[bytes] = []
        self.result = result or Transcript(text=SCAM_TEXT, usd=0.0)

    def __call__(self, data: bytes, language: str) -> Transcript | Rejection:
        self.calls.append(data)
        return self.result


@pytest.mark.parametrize(
    "media_case",
    [
        ("audio", opus(), "audio", "audio"),
        ("pdf", b"%PDF-1.4\n<< /Type /Page >>\n%%EOF\n", "pdf", "pdf"),
        ("imagen", _png(), "image", "image"),
    ],
)
async def test_media_is_read_by_its_bytes_then_judged(
    app_conn: asyncpg.Connection,
    recorder: Recorder,
    keyring: Keyring,
    media_case: tuple[str, bytes, str, str],
) -> None:
    fixture_name, media, reader, kind = media_case
    await team(app_conn, recorder)
    outbox, fake = Outbox(media), FakeReader()

    ch = channel(keyring, outbox, {reader: fake})
    await whatsapp.process(app_conn, ch, incoming(fixture_name, EMPLOYEE))

    [read] = fake.calls
    if kind == "audio":
        assert isinstance(check_wav(read, 120), float)  # ogg/opus became canonical WAV
    else:
        assert read == media
    assert outbox.to(EMPLOYEE)[0].startswith("Esto es una estafa")
    rows = await app_conn.fetch("SELECT kind, label FROM verdicts")
    assert [(r["kind"], r["label"]) for r in rows] == [(kind, "scam")]


@pytest.mark.parametrize(
    "media_case",
    [("pdf", b"%PDF-1.4\n<< /Type /Page >>\n%%EOF\n", "pdf"), ("imagen", _png(), "image")],
)
async def test_adv_filtro_contenido_media_gets_a_degraded_verdict(
    app_conn: asyncpg.Connection,
    recorder: Recorder,
    keyring: Keyring,
    media_case: tuple[str, bytes, str],
) -> None:
    """adv-filtro-contenido (T-09b): a filtered read answers a verdict, never silence."""
    fixture_name, media, reader = media_case
    await team(app_conn, recorder)
    outbox = Outbox(media)
    filtered = Rejection(error_code="CONTENT_FILTER", message="content_policy_violation")

    ch = channel(keyring, outbox, {reader: FakeReader(filtered)})
    await whatsapp.process(app_conn, ch, incoming(fixture_name, EMPLOYEE))

    [reply] = outbox.to(EMPLOYEE)
    assert reply.startswith(t("es", "verdict.doubtful.header"))
    assert t("es", "signal.model_could_not_verify") in reply
    rows = await app_conn.fetch("SELECT label FROM verdicts")
    assert [r["label"] for r in rows] == ["doubtful"]


async def test_an_image_qr_link_reaches_the_signals(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    read = Transcript(
        text="Escanea para pagar", usd=0.0, qr_urls=("https://bancolombia-seguro.co/x",)
    )
    outbox = Outbox(_png())

    ch = channel(keyring, outbox, {"image": FakeReader(read)})
    await whatsapp.process(app_conn, ch, incoming("imagen", EMPLOYEE))

    assert outbox.to(EMPLOYEE)[0].startswith("Esto es una estafa")


@pytest.mark.parametrize(
    "unreadable",
    [
        (b"\0" * (MAX_ATTACHMENT_BYTES + 1), "attachment.too_large"),
        (b"PK\x03\x04 a zip", "case.unsupported"),
        (b"OggS" + b"\0" * 100, "audio.unsupported"),
    ],
    ids=["too-large", "unknown-bytes", "broken-ogg"],
)
async def test_unreadable_media_gets_a_typed_reply(
    app_conn: asyncpg.Connection,
    recorder: Recorder,
    keyring: Keyring,
    unreadable: tuple[bytes, str],
) -> None:
    media, key = unreadable
    await team(app_conn, recorder)
    outbox = Outbox(media)

    await whatsapp.process(app_conn, channel(keyring, outbox), incoming("audio", EMPLOYEE))

    assert outbox.to(EMPLOYEE) == [t("es", key)]
    assert await app_conn.fetch("SELECT 1 FROM verdicts") == []


async def test_a_reader_failure_is_a_reply_not_a_crash(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    down = FakeReader(Rejection(error_code="MODEL_UNAVAILABLE", message="x", retryable=True))
    outbox = Outbox(b"%PDF-1.4\n%%EOF\n")

    ch = channel(keyring, outbox, {"pdf": down})
    await whatsapp.process(app_conn, ch, incoming("pdf", EMPLOYEE))

    assert outbox.to(EMPLOYEE) == [t("es", "pdf.unavailable")]


async def test_media_without_a_reader_is_unavailable(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox(b"%PDF-1.4\n%%EOF\n")

    await whatsapp.process(app_conn, channel(keyring, outbox), incoming("pdf", EMPLOYEE))

    assert outbox.to(EMPLOYEE) == [t("es", "pdf.unavailable")]


async def test_the_analyst_gets_the_business_address_book_and_the_media(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    await handle(
        app_conn, recorder.ports, message(OWNER, "proveedor Aceros SA correo=pagos@aceros.example")
    )
    seen: list[tuple[list[str], bytes | None]] = []

    def analyst(suppliers: Any, media: bytes | None) -> None:
        seen.append(([s.name for s in suppliers], media))

    outbox = Outbox(_png())
    ch = channel(keyring, outbox, {"image": FakeReader()})
    ch = whatsapp.Channel(**(ch.__dict__ | {"analyst": analyst}))

    await whatsapp.process(app_conn, ch, incoming("imagen", EMPLOYEE))

    assert seen == [(["Aceros SA"], _png())]


async def test_adv_media_repetida(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """The same file forwarded again (new message id) is a duplicate before any reader pays."""
    await team(app_conn, recorder)
    outbox, fake = Outbox(_png()), FakeReader()
    ch = channel(keyring, outbox, {"image": fake})

    for n in range(3):
        msg = incoming("imagen", EMPLOYEE, message_id=f"wamid.repeat{n}")
        await whatsapp.process(app_conn, ch, msg)

    assert len(fake.calls) == 1
    assert outbox.downloads == 3  # the bytes decide, so each copy is downloaded once
    assert outbox.to(EMPLOYEE)[1:] == [t("es", "case.duplicate")] * 2
    assert len(await app_conn.fetch("SELECT 1 FROM verdicts")) == 1


async def test_a_failed_read_releases_the_media_claim(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """A reader failure is not a duplicate: the same bytes sent again are read again."""
    await team(app_conn, recorder)
    down = Rejection(error_code="MODEL_UNAVAILABLE", message="x", retryable=True)
    outbox, fake = Outbox(_png()), FakeReader(down)
    ch = channel(keyring, outbox, {"image": fake})

    await whatsapp.process(app_conn, ch, incoming("imagen", EMPLOYEE, message_id="wamid.f0"))
    fake.result = Transcript(text=SCAM_TEXT, usd=0.0)
    await whatsapp.process(app_conn, ch, incoming("imagen", EMPLOYEE, message_id="wamid.f1"))

    assert len(fake.calls) == 2
    first, second = outbox.to(EMPLOYEE)
    assert first == t("es", "image.unavailable")
    assert second.startswith("Esto es una estafa")


async def test_a_file_refused_by_its_free_check_is_refused_again_not_duplicate(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox(b"%PDF-1.4\n" + b"/Type /Page\n" * 21 + b"%%EOF\n")
    ch = channel(keyring, outbox, {"pdf": FakeReader()})

    for n in range(2):
        await whatsapp.process(app_conn, ch, incoming("pdf", EMPLOYEE, message_id=f"wamid.p{n}"))

    assert outbox.to(EMPLOYEE) == [t("es", "pdf.too_large")] * 2


# --- estafa confirmada (owner feeds known campaigns, D-017) ---------------------------


async def _scam_case_id(conn: asyncpg.Connection, keyring: Keyring, outbox: Outbox) -> str:
    await whatsapp.process(conn, channel(keyring, outbox), incoming("texto", EMPLOYEE))
    found = re.search(r"caso (\d+)$", outbox.to(EMPLOYEE)[-1])
    assert found
    return found.group(1)


async def test_inc_id_caso_uuid_the_reply_quotes_a_short_case_number(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """Incident inc-id-caso-uuid: the reply quoted a 36-char UUID a shopkeeper cannot type."""
    await team(app_conn, recorder)
    outbox = Outbox()
    for n in range(2):
        msg = incoming("texto", EMPLOYEE, message_id=f"wamid.n{n}")
        await whatsapp.process(app_conn, channel(keyring, outbox), msg)

    replies = outbox.to(EMPLOYEE)
    assert [r.splitlines()[-1] for r in replies] == [
        t("es", "message.id_line", ref=ref) for ref in ("1", "2")
    ]
    assert not any(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", r) for r in replies)


async def test_scam_confirmed_feeds_known_campaigns_with_fingerprints_only(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    ref = await _scam_case_id(app_conn, keyring, outbox)

    confirm = incoming("texto", OWNER, body=f"estafa confirmada {ref}", message_id="wamid.c")
    await whatsapp.process(app_conn, channel(keyring, outbox), confirm)

    assert outbox.to(OWNER)[-1] == t("es", "scam_confirmed.done")
    case_id = await app_conn.fetchval("SELECT id::text FROM cases WHERE ref = $1", int(ref))
    assert case_id is not None
    rows = await app_conn.fetch(
        "SELECT id, country, kind, brand, simhash, domains, source_type, source_url, times_seen"
        " FROM known_campaigns WHERE source_type = 'usuario'"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["id"].startswith("usr-co-") and case_id not in row["id"]
    assert row["country"] == "CO" and row["brand"] == "Bancolombia"
    assert row["simhash"] is not None
    assert row["domains"] == ["bancolombia-seguro.co"]
    assert "0702" not in str(dict(row)) and "bloqueada" not in str(dict(row))


async def test_another_business_cannot_confirm_a_case_by_its_number(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """Case numbers repeat across businesses (D-091): only RLS keeps ``caso 1`` apart."""
    await team(app_conn, recorder)
    outbox = Outbox()
    ref = await _scam_case_id(app_conn, keyring, outbox)
    other = "+573000000801"
    await handle(app_conn, recorder.ports, message(other, "hola Panaderia La Espiga CO"))

    confirm = incoming("texto", other, body=f"estafa confirmada {ref}", message_id="wamid.x")
    await whatsapp.process(app_conn, channel(keyring, outbox), confirm)

    assert outbox.to(other)[-1] == t("es", "scam_confirmed.unknown")
    assert not await app_conn.fetchval("SELECT count(*) FROM known_campaigns")


async def test_confirming_twice_counts_it_twice(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    case_id = await _scam_case_id(app_conn, keyring, outbox)
    for n in range(2):
        confirm = incoming("texto", OWNER, body=f"estafa confirmada {case_id}", message_id=f"c{n}")
        await whatsapp.process(app_conn, channel(keyring, outbox), confirm)

    rows = await app_conn.fetch(
        "SELECT times_seen FROM known_campaigns WHERE source_type = 'usuario'"
    )
    assert [r["times_seen"] for r in rows] == [2]


async def test_only_the_owner_confirms_a_scam(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    case_id = await _scam_case_id(app_conn, keyring, outbox)

    confirm = incoming("texto", EMPLOYEE, body=f"estafa confirmada {case_id}", message_id="c")
    await whatsapp.process(app_conn, channel(keyring, outbox), confirm)

    assert outbox.to(EMPLOYEE)[-1] == t("es", "command.owner_only")
    assert await app_conn.fetch("SELECT 1 FROM known_campaigns WHERE source_type = 'usuario'") == []


async def test_an_unknown_id_or_a_case_without_fingerprint_feeds_nothing(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    short = incoming("texto", EMPLOYEE, body="me llamaron raro hoy")
    await whatsapp.process(app_conn, channel(keyring, outbox), short)
    found = re.search(r"caso (\d+)$", outbox.to(EMPLOYEE)[-1])
    assert found

    tries = [
        (found.group(1), "scam_confirmed.nothing"),
        ("999", "scam_confirmed.unknown"),
    ]
    for n, (case_id, key) in enumerate(tries):
        confirm = incoming("texto", OWNER, body=f"estafa confirmada {case_id}", message_id=f"c{n}")
        await whatsapp.process(app_conn, channel(keyring, outbox), confirm)
        assert outbox.to(OWNER)[-1] == t("es", key)

    assert await app_conn.fetch("SELECT 1 FROM known_campaigns WHERE source_type = 'usuario'") == []


async def test_a_confirmed_campaign_reaches_the_next_message(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    await team(app_conn, recorder)
    outbox = Outbox()
    case_id = await _scam_case_id(app_conn, keyring, outbox)
    confirm = incoming("texto", OWNER, body=f"estafa confirmada {case_id}", message_id="c")
    await whatsapp.process(app_conn, channel(keyring, outbox), confirm)

    mine = keyring.hash(str(await app_conn.fetchval("SELECT business_id FROM members LIMIT 1")))
    pack = await whatsapp.business_pack(app_conn, Settings(), "CO", mine)

    assert any(c.id.startswith("usr-co-") for c in pack.campaigns)


async def test_one_business_alone_cannot_poison_the_others(
    app_conn: asyncpg.Connection, keyring: Keyring
) -> None:
    pack = await whatsapp.business_pack(app_conn, Settings(), "CO", keyring.hash("b1"))
    campaign = confirmed_campaign(SCAM_TEXT, pack, "Bancolombia", date(2026, 10, 28))
    assert campaign is not None

    def ids(found: list[KnownCampaign]) -> list[str]:
        return [c.id for c in found if c.id == campaign.id]

    await store_knowledge.confirm_campaign(app_conn, campaign, keyring.hash("b1"))
    assert ids(await store_knowledge.fetch_known_campaigns(app_conn, "CO", keyring.hash("b1")))
    assert not ids(await store_knowledge.fetch_known_campaigns(app_conn, "CO", keyring.hash("b2")))
    assert not ids(await store_knowledge.fetch_known_campaigns(app_conn, "CO"))

    await store_knowledge.confirm_campaign(app_conn, campaign, keyring.hash("b2"))
    assert ids(await store_knowledge.fetch_known_campaigns(app_conn, "CO", keyring.hash("b3")))


def test_received_at_is_kept_from_the_channel() -> None:
    msg = incoming("texto", EMPLOYEE, timestamp="2026-10-28T14:03:11Z")
    assert msg.received_at == datetime(2026, 10, 28, 14, 3, 11, tzinfo=UTC)
    assert datetime.now(UTC) - msg.received_at > timedelta(0)
    assert Path(__file__).exists()


# --- T-10e: attachments without media_url, found by listing --------------------------


def _listed(*kinds: str) -> list[dict[str, Any]]:
    """Inbound media items from EMPLOYEE, one second after the webhook (newest first)."""
    item = fixture("mensajes_media")["response"][0] | {"contact_number": EMPLOYEE[1:]}
    ids = ("00000000-0000-4000-8000-00000000b001", "00000000-0000-4000-8000-00000000b009")
    return [item | {"media_type": k, "id": i} for k, i in zip(kinds, ids, strict=False)]


def _bomb() -> bytes:
    """A tiny real PNG whose header declares a 13000×13000 canvas (CRC recomputed)."""
    buf = io.BytesIO()
    Image.new("L", (1, 1), 255).save(buf, "PNG")
    png = bytearray(buf.getvalue())
    png[16:24] = (13000).to_bytes(4, "big") * 2
    png[29:33] = zlib.crc32(bytes(png[12:29])).to_bytes(4, "big")
    return bytes(png)


async def test_t10e_a_voice_note_without_url_is_read_and_judged(
    app_conn: asyncpg.Connection, recorder: Recorder, keyring: Keyring
) -> None:
    """T-10e incident: the real webhook brought `[audio]` and was judged as text."""
    await team(app_conn, recorder)
    outbox, fake = Outbox(opus(), _listed("audio")), FakeReader()

    ch = channel(keyring, outbox, {"audio": fake})
    await whatsapp.process(app_conn, ch, incoming("audio_sin_url", EMPLOYEE))

    [read] = fake.calls
    assert isinstance(check_wav(read, 120), float)
    assert outbox.to(EMPLOYEE)[0].startswith("Esto es una estafa")
    rows = await app_conn.fetch("SELECT kind, label FROM verdicts")
    assert [(r["kind"], r["label"]) for r in rows] == [("audio", "scam")]


@pytest.mark.parametrize(
    "listing", [[], _listed("audio", "image")], ids=["nothing-listed", "two-attachments"]
)
async def test_t10e_an_unclear_match_is_said_never_guessed(
    app_conn: asyncpg.Connection,
    recorder: Recorder,
    keyring: Keyring,
    listing: list[dict[str, Any]],
) -> None:
    await team(app_conn, recorder)
    outbox, fake = Outbox(opus(), listing), FakeReader()

    ch = channel(keyring, outbox, {"audio": fake, "image": fake})
    await whatsapp.process(app_conn, ch, incoming("audio_sin_url", EMPLOYEE))

    assert outbox.to(EMPLOYEE) == [t("es", "attachment.unmatched")]
    assert fake.calls == [] and outbox.downloads == 0
    assert await app_conn.fetch("SELECT 1 FROM verdicts") == []


@pytest.mark.parametrize(
    "unreadable",
    [
        (_bomb(), "image.too_large"),
        (b"PK\x03\x04 a zip", "case.unsupported"),
        (b"\0" * (MAX_ATTACHMENT_BYTES + 1), "attachment.too_large"),
    ],
    ids=["pixel-bomb", "real-type-by-bytes", "too-large"],
)
async def test_t10e_listed_media_gets_the_same_checks_as_caso(
    app_conn: asyncpg.Connection,
    recorder: Recorder,
    keyring: Keyring,
    unreadable: tuple[bytes, str],
) -> None:
    media, key = unreadable
    await team(app_conn, recorder)
    outbox, fake = Outbox(media, _listed("image")), FakeReader()

    ch = channel(keyring, outbox, {"image": fake})
    await whatsapp.process(app_conn, ch, incoming("audio_sin_url", EMPLOYEE, body="[image]"))

    assert outbox.to(EMPLOYEE) == [t("es", key)]
    assert fake.calls == []
    assert await app_conn.fetch("SELECT 1 FROM verdicts") == []
