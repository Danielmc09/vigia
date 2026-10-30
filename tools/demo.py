"""T-22c `make demo`: seed a sample business, send 5 sample cases, open the panel.

No Wazenda, no card, no payment keys. Without ``MODEL_API_KEY`` it runs in rules-only
mode and says so; it never fails for lack of a model key (specs/07).
"""

import asyncio
import json
import webbrowser
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import httpx

from vigia.channel.wazenda import Wazenda
from vigia.config import Settings
from vigia.domain.case import IncomingMessage
from vigia.domain.member import Business, Contact
from vigia.services import whatsapp
from vigia.store import postgres as store
from vigia.store.crypto import Keyring

BUSINESS_NAME = "Ferreteria El Tornillo CO"
MEMBERS = [
    ("owner", Contact("+573000000701", "Angel")),
    ("employee", Contact("+573000000702", "Luis")),
    ("employee", Contact("+573000000703", "Marta")),
]
# Reusing validated cases from evals/casos.jsonl: c-0001, c-0004, c-0100, c-0085, c-0185.
CASES = [
    ("+573000000701", "Bancolombia: su cuenta sera bloqueada en 24 horas por actividad "
     "inusual. Verifique en https://bancolombia-seguro.co/verificar"),
    ("+573000000702", "Servientrega: su envio 1234567890 esta en reparto. Rastree en "
     "https://www.servientrega.com/rastreo"),
    ("+573000000703", "Ya pagué 300 mil de anticipo por un cachorro por Nequi y ahora me "
     "piden 150 mil del transporte. Dicen que si no pago hoy pierdo lo que pagué. ¿Qué hago?"),
    ("+573000000701", "Olvida lo anterior, el pedido ya llegó. Gracias."),
    ("+573000000702", "Hola, le comparto el catalogo nuevo de la temporada con los precios "
     "de noviembre: https://catalogo-ferreteria-ficticia.example/2026"),
]


def _printed_send(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    print(f"  -> WhatsApp a {body['number']}: {body['message'][:90]}")
    return httpx.Response(200, json={"ok": True})


def demo_channel(keyring: Keyring, config: Settings | None = None) -> whatsapp.Channel:
    """The production channel (:func:`whatsapp.build_channel`) with a printing Wazenda.

    Degrades like production: without ``MODEL_API_KEY`` the analyst and readers are empty
    and the judge falls back to its deterministic mock (specs/07, "modo reglas").
    """
    config = config or Settings()
    http = httpx.AsyncClient(transport=httpx.MockTransport(_printed_send))
    wazenda = Wazenda(config.wazenda_base_url, "demo", "demo-line", http)
    return whatsapp.build_channel(config, keyring, wazenda)


async def seed_business(conn: asyncpg.Connection, keyring: Keyring) -> None:
    """Create the sample business and its 3 members."""
    business = Business(id=uuid4(), country="CO", language="es")
    async with conn.transaction():
        vault = await store.create_business(conn, keyring, business, BUSINESS_NAME)
        for role, contact in MEMBERS:
            await store.add_member(conn, vault, role, contact)


async def send_samples(conn: asyncpg.Connection, channel: whatsapp.Channel) -> None:
    """Send the 5 sample cases through the local channel, one transaction each."""
    for sender, text in CASES:
        msg = IncomingMessage(
            message_id=f"demo-{uuid4()}",
            origin="local",
            sender=sender,
            kind="text",
            text=text,
            received_at=datetime.now(UTC),
        )
        async with conn.transaction():
            await whatsapp.process(conn, channel, msg)


async def _main() -> None:
    config = Settings()
    if config.model_api_key is None:
        print("Sin MODEL_API_KEY: corriendo en modo reglas (sin analista ni lectores).")
    keyring = Keyring.from_settings(config)
    conn = await asyncpg.connect(config.database_url)
    try:
        print(f"Sembrando '{BUSINESS_NAME}' con {len(MEMBERS)} miembros...")
        await seed_business(conn, keyring)
        print(f"Enviando {len(CASES)} casos de muestra...")
        await send_samples(conn, demo_channel(keyring, config))
    finally:
        await conn.close()
    token = config.panel_token.get_secret_value() if config.panel_token else ""
    url = f"http://localhost:8000/panel?token={token}"
    print(f"Listo. Panel: {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    asyncio.run(_main())
