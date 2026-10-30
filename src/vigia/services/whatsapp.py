"""WhatsApp pipeline behind the Wazenda webhook (specs/01, application).

Member check → media download and reading (bytes decide the kind) → ingestion → gate,
rules and analyst → stored verdict → code-built reply → team alert on a scam (rule 14).
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import NamedTuple

import asyncpg
import httpx

from vigia import telemetry
from vigia.channel.templates import team_alert_message, whatsapp_message
from vigia.channel.wazenda import HTTP_TIMEOUT_S, Wazenda, ogg_to_wav
from vigia.config import Settings
from vigia.domain.attachment import Attachment, sniff
from vigia.domain.case import Case, IncomingMessage
from vigia.domain.country import CountryPack
from vigia.domain.gate import Judge
from vigia.domain.member import Supplier, normalize_contact
from vigia.domain.reading import Transcriber, Transcript
from vigia.domain.rejection import Rejection
from vigia.i18n import t
from vigia.model.sam import make_sam
from vigia.model.spark import make_spark
from vigia.model.voice import make_voice
from vigia.policy.actions import should_alert_team
from vigia.services import ingest, transcription
from vigia.services.backend import make_chat
from vigia.services.judge import make_judge
from vigia.services.process_case import Analyst, judge_case, spark_analyst
from vigia.signals.knowledge import seeded_pack, with_campaigns
from vigia.store import knowledge as store_knowledge
from vigia.store import postgres as store
from vigia.store.crypto import Hashed, Keyring, Vault
from vigia.verdict.fusion import SCAM

log = logging.getLogger(__name__)

AnalystFactory = Callable[[Sequence[Supplier], bytes | None], Analyst | None]
"""The analyst for one case, given the business's address book and the checked media."""

_READER = {"audio": "audio", "document": "pdf", "image": "image"}


@dataclass(frozen=True)
class WebhookPorts:
    """What ``POST /webhook/wazenda`` needs once the signature checks (tests inject fakes).

    Attributes:
        first_time: Whether a channel message id is new (idempotency, specs/01).
        process: Runs one message through the pipeline, in the background.
        close: Releases what the ports hold (the HTTP client) at shutdown.
    """

    first_time: Callable[[str], Awaitable[bool]]
    process: Callable[[IncomingMessage], Awaitable[None]]
    close: Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class Channel:
    """Everything :func:`process` talks to.

    Attributes:
        keyring: Process keys.
        wazenda: Media download and sending.
        config: Settings (audio and PDF caps, knowledge seed).
        readers: Voice, PDF and image readers keyed ``audio``, ``pdf``, ``image``.
        analyst: Builds the analyst per case; returns ``None`` without a model.
        judge: Spark judge (eval/prod) or mock for the gate.
    """

    keyring: Keyring
    wazenda: Wazenda
    config: Settings
    readers: Mapping[str, Transcriber]
    analyst: AnalystFactory
    judge: Judge


async def business_pack(
    conn: asyncpg.Connection, config: Settings, country: str, business: Hashed
) -> CountryPack:
    """The country pack with the seed campaigns and the ones this business may see (D-082).

    Args:
        conn: Connection with the application role.
        config: Settings with the knowledge seed.
        country: ISO code of the business.
        business: ``Keyring.hash`` of the business id.

    Returns:
        The pack.

    Raises:
        LookupError: The country has no pack (enrollment only accepts known ones).
    """
    pack = seeded_pack(country, config.knowledge_seed)
    if pack is None:
        raise LookupError(f"no country pack for {country!r}")
    stored = await store_knowledge.fetch_known_campaigns(conn, country, business)
    merged = {c.id: c for c in (*pack.campaigns, *stored)}
    return with_campaigns(pack, merged.values())


async def _first_media(conn: asyncpg.Connection, vault: Vault, data: bytes) -> bool:
    """Claim a file for its business; a copy already claimed is a duplicate (adv-media-repetida).

    The claim commits with the message's transaction, so a concurrent copy waits on it;
    :func:`process` rolls it back when the read does not produce text.
    """
    digest = hashlib.sha256(data).hexdigest()
    return await store.first_webhook_event(conn, vault.hash(f"media:{vault.business_id}:{digest}"))


class MediaRead(NamedTuple):
    """An attachment turned into text.

    Attributes:
        msg: The message with its ``kind``, ``text`` and checked attachment.
        media: The checked bytes (the analyst's ``crop`` reuses them) and their transcript.
    """

    msg: IncomingMessage
    media: "Media"


async def _read_media(
    channel: Channel, msg: IncomingMessage, language: str, first: Callable[[bytes], Awaitable[bool]]
) -> MediaRead | str:
    """Download the attachment and turn it into text; a string is the reply instead.

    ``first`` claims the bytes before any reader is paid; a repeat is answered as a duplicate.
    """
    data = await channel.wazenda.download(msg.attachments[0].ref or "")
    if isinstance(data, Rejection):
        too_large = data.error_code == "TOO_LARGE"
        return t(language, "attachment.too_large" if too_large else "attachment.unavailable")
    read = await read_media(data, channel.readers, channel.config, Sender(language, first))
    if isinstance(read, str):
        return t(language, read)
    attachment = Attachment(ref=msg.attachments[0].ref, mime=read.mime)
    update = {"kind": read.kind, "text": read.transcript.text, "attachments": [attachment]}
    return MediaRead(msg.model_copy(update=update), read)


class Media(NamedTuple):
    """Checked bytes and what their reader made of them.

    Attributes:
        kind: ``audio``, ``image`` or ``document``, from the magic bytes.
        mime: MIME of ``data`` (an ogg voice note arrives here as ``audio/wav``).
        data: The checked bytes.
        transcript: The reader's text, QR links, cost and refusal.
    """

    kind: str
    mime: str
    data: bytes
    transcript: Transcript


class Sender(NamedTuple):
    """Who a file comes from, as its reader needs it.

    Attributes:
        language: Language hint for the reader (Voice's bias).
        first: Claims the bytes before any reader is paid; a repeat is a duplicate.
    """

    language: str
    first: Callable[[bytes], Awaitable[bool]]


async def read_media(
    data: bytes, readers: Mapping[str, Transcriber], config: Settings, sender: Sender
) -> Media | str:
    """Turn a file into text, the same for WhatsApp and the panel; a string is an i18n key.

    Args:
        data: The file, already under the attachment cap.
        readers: :func:`model_readers` (fakes in tests).
        config: Settings with the audio and PDF caps.
        sender: Language and dedupe claim of whoever sent it.

    Returns:
        The read file, or the i18n key of the reply when it cannot be read.
    """
    found = sniff(data)
    if found is None:
        return "case.unsupported"
    kind, mime = found
    if mime == "audio/ogg":
        wav = ogg_to_wav(data, config.voice_max_seconds)
        if isinstance(wav, Rejection):
            return transcription.reply_key(wav)
        data, mime = wav, "audio/wav"
    reader_kind = _READER[kind]
    if reader_kind not in readers:
        return f"{reader_kind}.unavailable"
    if not await sender.first(data):
        return "case.duplicate"
    reader = transcription.checked(reader_kind, readers[reader_kind], config)
    read = await asyncio.to_thread(reader, data, sender.language)
    if isinstance(read, Rejection):
        return transcription.reply_key(read, transcription.REPLY_KEYS[reader_kind])
    telemetry.add_usd(read.usd)
    return Media(kind, mime, data, read)


async def process(conn: asyncpg.Connection, channel: Channel, msg: IncomingMessage) -> None:
    """One WhatsApp message, end to end (specs/01 step 9).

    Args:
        conn: Application connection inside a transaction.
        channel: Outbound ports and readers.
        msg: Message parsed from a verified webhook.
    """
    with telemetry.scope():
        await _process(conn, channel, msg)


async def _process(conn: asyncpg.Connection, channel: Channel, msg: IncomingMessage) -> None:
    """:func:`process` under one trace."""
    sender = normalize_contact(msg.sender)
    media: bytes | None = None
    qr: tuple[str, ...] = ()
    refused = False
    if msg.attachments:
        member = await store.find_member(conn, channel.keyring.hash(sender))
        if member is not None:  # strangers cost no download (ingest drops them below)
            business, vault = await store.open_business(conn, channel.keyring, member.business_id)
            first = partial(_first_media, conn, vault)
            claim = conn.transaction()  # savepoint: the media claim lives only if the read does
            await claim.start()
            with telemetry.stage("gate") as cost:
                read = await _read_media(channel, msg, business.language, first)
                cost["usd"] = read.media.transcript.usd if not isinstance(read, str) else 0.0
            if isinstance(read, str):
                await claim.rollback()  # a refused or failed read can be sent again (D-083)
                await channel.wazenda.send(sender, read)
                return
            await claim.commit()
            found = read.media.transcript
            msg, media, qr, refused = read.msg, read.media.data, found.qr_urls, found.refused
    cases: list[Case] = []

    async def capture(case: Case) -> None:
        cases.append(case)

    async def no_email(to: str, body: str) -> None:
        log.warning("email_sender_missing channel=wazenda")  # ponytail: Resend arrives with T-17

    with telemetry.stage("gate"):
        reply = await ingest.handle(conn, ingest.Ports(channel.keyring, no_email, capture), msg)
    if reply is not None:
        await channel.wazenda.send(sender, reply)
    for case in cases:
        update = {"qr_urls": list(qr), "model_refused": refused}
        await _judge(conn, channel, case.model_copy(update=update), media)


async def _judge(
    conn: asyncpg.Connection, channel: Channel, case: Case, media: bytes | None
) -> None:
    """Verdict, store, reply, and the team alert when it is a new scam of its brand."""
    with telemetry.scope(case_id=str(case.id), business_id=str(case.business_id)):
        await _judge_traced(conn, channel, case, media)


async def _judge_traced(
    conn: asyncpg.Connection, channel: Channel, case: Case, media: bytes | None
) -> None:
    """:func:`_judge` with the case ids bound."""
    business, vault = await store.open_business(conn, channel.keyring, case.business_id)
    lang = business.language
    pack = await business_pack(conn, channel.config, case.country, vault.hash(str(business.id)))
    analyst = channel.analyst(await store.suppliers(conn, vault), media)
    # ponytail: the transaction stays open while the models run; a pool and short
    # transactions when traffic needs them (T-16).
    verdict = await asyncio.to_thread(judge_case, case, pack, channel.judge, analyst)
    last = await store.last_verdict_at(conn, SCAM, verdict.impersonated_brand)
    await store.save_verdict(conn, vault, case, verdict)
    sender = normalize_contact(case.sender or "")
    with telemetry.stage("reply"):
        await channel.wazenda.send(sender, whatsapp_message(verdict, lang, case_ref=case.ref))
    with telemetry.stage("policy"):
        alert_team = verdict.label == SCAM and should_alert_team(last, datetime.now(UTC))
    if not alert_team:
        return
    team = await store.team(conn, vault)
    reporter = next((c.name for c in team if c.phone == sender and c.name), None)
    alert = team_alert_message(
        lang,
        reporter or t(lang, "team_alert.someone"),
        t(lang, f"team_alert.kind.{case.kind}"),
        verdict.impersonated_brand,
    )
    with telemetry.stage("reply"):
        for contact in team:
            if contact.phone != sender:
                await channel.wazenda.send(contact.phone, alert)
    log.info("team_alerted from=%s members=%d", telemetry.mask_phone(sender), len(team) - 1)


def model_readers(config: Settings) -> dict[str, Transcriber]:
    """Voice, Spark's PDF reader and the image reader, whichever has a key behind it.

    Args:
        config: Settings with ``MODEL_API_KEY``.

    Returns:
        Readers keyed ``audio``, ``pdf`` and ``image``; empty without a key.
    """
    voice, spark = make_voice(config), make_spark(config)
    found: dict[str, Transcriber | None] = {
        "audio": voice,
        "pdf": spark.read_pdf if spark else None,
        "image": transcription.image_reader(spark.read_image, make_sam(config)) if spark else None,
    }
    return {kind: reader for kind, reader in found.items() if reader is not None}


def build_channel(config: Settings, keyring: Keyring, wazenda: Wazenda) -> Channel:
    """The channel with whatever Meta models have a key: analyst, readers and judge.

    Args:
        config: Settings with ``MODEL_API_KEY``; without it, rules only.
        keyring: Process keys.
        wazenda: Real client, or a local one (``make demo``).

    Returns:
        The channel :func:`process` talks to.
    """
    spark, sam = make_chat(config), make_sam(config)

    def analyst(suppliers: Sequence[Supplier], media: bytes | None) -> Analyst | None:
        if spark is None:
            return None
        load = (lambda _ref: media) if media else None  # crop reuses the checked bytes
        return spark_analyst(spark, suppliers, sam, load)

    return Channel(keyring, wazenda, config, model_readers(config), analyst, make_judge(config))


def live(config: Settings, metrics: telemetry.Metrics) -> WebhookPorts:
    """The real webhook ports: Postgres, Wazenda, Meta models (built on the first request).

    Args:
        config: Settings with the Wazenda and model keys.
        metrics: The app's instruments; each message is recorded into them.

    Returns:
        Ports that open their own connection per call.

    Raises:
        RuntimeError: ``WAZENDA_API_KEY`` or ``WAZENDA_LINE_ID`` is missing.
    """
    if config.wazenda_api_key is None or config.wazenda_line_id is None:
        raise RuntimeError("WAZENDA_API_KEY and WAZENDA_LINE_ID are required")
    keyring = Keyring.from_settings(config)
    http = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
    wazenda = Wazenda(
        config.wazenda_base_url,
        config.wazenda_api_key.get_secret_value(),
        config.wazenda_line_id,
        http,
    )
    channel = build_channel(config, keyring, wazenda)

    async def first_time(message_id: str) -> bool:
        conn = await asyncpg.connect(config.database_url)
        try:
            return await store.first_webhook_event(conn, keyring.hash(f"wazenda:{message_id}"))
        finally:
            await conn.close()

    async def run(msg: IncomingMessage) -> None:
        conn = await asyncpg.connect(config.database_url)
        try:
            async with conn.transaction():
                with telemetry.recording(metrics):
                    await process(conn, channel, msg)
        finally:
            await conn.close()

    return WebhookPorts(first_time, run, http.aclose)
