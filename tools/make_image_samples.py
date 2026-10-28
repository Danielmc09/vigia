"""Write the synthetic screenshot eval samples c-0189..c-0210 and print their eval lines (T-12).

Each PNG is a 600x900 "phone screenshot": a drawn placeholder logo (a coloured badge with
initials, never a real logo), a few lines of text and, for some, a QR. Names, accounts and
domains are fictitious. One QR is too small and tilted to read in the whole screenshot:
only the SAM crop recovers it (D-076). Usage::

    uv run python tools/make_image_samples.py > /tmp/image_cases.jsonl
"""

import io
import json
import sys
from pathlib import Path
from typing import NamedTuple

import zxingcpp
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).parents[1]
SAMPLES = REPO / "evals" / "muestras"
WIDTH, HEIGHT = 600, 900


class Sample(NamedTuple):
    """One screenshot and its eval fields."""

    case_id: str
    pais: str
    esperado: str
    categoria: str
    marca: str | None
    hipotesis: str
    lines: list[str]
    badge: tuple[str, str]
    """Initials and fill colour of the drawn placeholder logo."""
    qr: str | None = None
    tiny_qr: bool = False


_QR_ONLY = "URL maliciosa solo en el QR (el texto visible no trae link)"

SAMPLES_SPEC: list[Sample] = [
    Sample(
        "c-0189",
        "CO",
        "estafa",
        "banco",
        "Bancolombia",
        f"{_QR_ONLY}: QR a bancolombia-actualiza.co -> marca en host fuera de dominio (dura)"
        " + qr_to_unofficial_domain -> estafa",
        [
            "Bancolombia",
            "Actualice sus datos hoy",
            "Escanee el codigo con su celular",
            "para evitar el bloqueo de su cuenta.",
        ],
        ("BC", "#1f4e9c"),
        qr="https://bancolombia-actualiza.co/datos",
    ),
    Sample(
        "c-0190",
        "CO",
        "estafa",
        "estado",
        "DIAN",
        f"{_QR_ONLY}: QR a dian-pagos-co.com -> marca en host fuera de dian.gov.co (dura)"
        " -> estafa",
        [
            "DIAN",
            "Sancion pendiente No. 2231",
            "Pague escaneando este codigo QR",
            "antes de 24 horas o se embargaran sus cuentas.",
        ],
        ("DN", "#2e7d32"),
        qr="https://dian-pagos-co.com/sancion/2231",
    ),
    Sample(
        "c-0191",
        "CO",
        "estafa",
        "transportadora",
        "Servientrega",
        f"{_QR_ONLY}: QR a servientrega-entregas.com -> marca en host fuera de dominio (dura)"
        " -> estafa",
        [
            "Servientrega",
            "Su paquete esta retenido",
            "Escanee para pagar el arancel de $8.900",
            "y reprogramar la entrega.",
        ],
        ("SV", "#2e7d32"),
        qr="https://servientrega-entregas.com/arancel",
    ),
    Sample(
        "c-0192",
        "CO",
        "estafa",
        "billetera",
        "Nequi",
        f"{_QR_ONLY}; QR pequeño e inclinado que solo se lee tras el recorte de SAM:"
        " nequi-premios.co -> marca en host fuera de nequi.com.co (dura) -> estafa",
        [
            "Nequi",
            "Ganaste un bono de $200.000",
            "Reclamalo escaneando el codigo de abajo",
            "Valido solo por hoy.",
        ],
        ("NQ", "#6a1b9a"),
        qr="https://nequi-premios.co/bono",
        tiny_qr=True,
    ),
    Sample(
        "c-0193",
        "US",
        "estafa",
        "transportadora",
        "USPS",
        f"{_QR_ONLY}: QR a usps-redelivery-help.com -> brand in host off usps.com (hard) -> estafa",
        [
            "USPS",
            "Your package could not be delivered",
            "Scan the code to schedule",
            "a new delivery. A $1.99 fee applies.",
        ],
        ("US", "#0d47a1"),
        qr="https://usps-redelivery-help.com/fee",
    ),
    Sample(
        "c-0194",
        "US",
        "estafa",
        "banco",
        "Chase",
        f"{_QR_ONLY}: QR a chase-verify-login.com -> brand in host off chase.com (hard) -> estafa",
        [
            "Chase",
            "Unusual sign-in detected",
            "Scan this code to verify your identity",
            "or your account will be locked.",
        ],
        ("CH", "#0d47a1"),
        qr="https://chase-verify-login.com/id",
    ),
    Sample(
        "c-0195",
        "CO",
        "estafa",
        "banco",
        "Davivienda",
        "captura de SMS con link visible davivienda-seguridad.co -> marca en host (dura) -> estafa",
        [
            "Mensaje de texto",
            "Davivienda: su clave vence hoy.",
            "Actualicela en",
            "davivienda-seguridad.co/clave",
        ],
        ("DV", "#c62828"),
    ),
    Sample(
        "c-0196",
        "US",
        "estafa",
        "comercio",
        "Amazon",
        "screenshot of a text with the link amazon-refund-center.com visible -> brand in host"
        " (hard) -> estafa",
        [
            "Text message",
            "Amazon: a refund of $129.99 is waiting.",
            "Claim it at",
            "amazon-refund-center.com/claim",
        ],
        ("AZ", "#ef6c00"),
    ),
    Sample(
        "c-0197",
        "CO",
        "estafa",
        "familiar",
        None,
        "captura de chat 'cambie de numero' pidiendo consignacion urgente a cuenta personal,"
        " sin link -> analista estafa",
        [
            "Chat",
            "Hola ma, cambie de numero, guarda este.",
            "Necesito que me consignes $450.000",
            "hoy a esta cuenta Nequi 300 000 0000, luego te explico.",
        ],
        ("CH", "#455a64"),
    ),
    Sample(
        "c-0198",
        "US",
        "estafa",
        "premio",
        None,
        "flyer with a QR to a prize site outside any official domain + 'gift card fee'"
        " -> qr_to_unofficial_domain media + analyst scam -> estafa",
        [
            "Congratulations!",
            "You won a $500 gift card",
            "Scan to claim and pay the",
            "$4.99 processing fee.",
        ],
        ("GC", "#ad1457"),
        qr="https://prize-claim-center.top/gift",
    ),
    Sample(
        "c-0199",
        "CO",
        "estafa",
        "telco",
        "Claro",
        "captura con link visible claro-facturas.co y amenaza de corte -> marca en host (dura)"
        " -> estafa",
        [
            "Claro",
            "Su servicio sera suspendido hoy",
            "Pague su factura en",
            "claro-facturas.co/pago",
        ],
        ("CL", "#c62828"),
    ),
    Sample(
        "c-0200",
        "CO",
        "legitimo",
        "legitimo_banco",
        "Bancolombia",
        "QR al dominio oficial www.bancolombia.com -> solo señales leves, sin presión -> legitimo",
        [
            "Bancolombia",
            "Descarga nuestra app",
            "Escanea el codigo para conocer",
            "nuestros canales oficiales.",
        ],
        ("BC", "#1f4e9c"),
        qr="https://www.bancolombia.com/personas",
    ),
    Sample(
        "c-0201",
        "US",
        "legitimo",
        "legitimo_transportadora",
        "USPS",
        "QR to the official www.usps.com tracking page -> light signals only -> legitimo",
        ["USPS", "Track your packages", "Scan to open USPS Tracking", "on the official site."],
        ("US", "#0d47a1"),
        qr="https://www.usps.com/manage/",
    ),
    Sample(
        "c-0202",
        "CO",
        "legitimo",
        "legitimo_otro",
        "DIAN",
        "afiche con QR al dominio oficial www.dian.gov.co, recordatorio de calendario"
        " tributario sin cobro -> legitimo",
        [
            "DIAN",
            "Calendario tributario",
            "Consulte las fechas de su declaracion",
            "escaneando el codigo.",
        ],
        ("DN", "#2e7d32"),
        qr="https://www.dian.gov.co/",
    ),
    Sample(
        "c-0203",
        "CO",
        "legitimo",
        "legitimo_otro",
        None,
        "aviso de horario de la tienda, sin link ni pedido de dinero -> legitimo",
        [
            "Ferreteria Ficticia El Tornillo",
            "Horario de fin de año",
            "Lunes a sabado 8 am - 6 pm",
            "Domingo cerrado. Gracias por su compra.",
        ],
        ("TF", "#795548"),
    ),
    Sample(
        "c-0204",
        "US",
        "legitimo",
        "legitimo_otro",
        None,
        "team meeting flyer, no link, no money -> legitimo",
        [
            "Fictitia Hardware",
            "Team meeting Friday 3 pm",
            "Back office, bring your",
            "inventory sheets. Pizza included.",
        ],
        ("FH", "#00695c"),
    ),
    Sample(
        "c-0205",
        "CO",
        "legitimo",
        "legitimo_proveedor",
        None,
        "confirmacion de pedido del proveedor sin cambio de cuenta ni link -> legitimo",
        [
            "Distribuidora Ficticia del Valle",
            "Pedido 4471 confirmado",
            "Despacho el jueves.",
            "El pago sigue por la cuenta de siempre.",
        ],
        ("DF", "#37474f"),
    ),
    Sample(
        "c-0206",
        "US",
        "legitimo",
        "legitimo_proveedor",
        None,
        "supplier delivery note, no link, no new account -> legitimo",
        [
            "Fictitia Supply Co.",
            "Delivery note #8812",
            "12 boxes of screws delivered",
            "Invoice to follow as usual.",
        ],
        ("FS", "#4e342e"),
    ),
    Sample(
        "c-0207",
        "CO",
        "legitimo",
        "legitimo_otro",
        None,
        "menu del restaurante del barrio sin link ni pago -> legitimo",
        [
            "Restaurante La Olla Ficticia",
            "Menu del dia $15.000",
            "Sopa, seco y jugo",
            "Domicilios al mismo numero de siempre.",
        ],
        ("LO", "#ef6c00"),
    ),
    Sample(
        "c-0208",
        "US",
        "legitimo",
        "legitimo_otro",
        None,
        "store holiday hours sign, no link -> legitimo",
        [
            "Fictitia Corner Shop",
            "Holiday hours",
            "Open 9 am to 5 pm",
            "Closed on Thanksgiving Day.",
        ],
        ("SC", "#f9a825"),
    ),
    Sample(
        "c-0209",
        "US",
        "legitimo",
        "legitimo_banco",
        "Chase",
        "QR to the official www.chase.com -> light signals only, no pressure -> legitimo",
        ["Chase", "Bank on the go", "Scan to learn about the", "official Chase Mobile app."],
        ("CH", "#0d47a1"),
        qr="https://www.chase.com/digital/mobile-banking",
    ),
    Sample(
        "c-0210",
        "CO",
        "legitimo",
        "legitimo_transportadora",
        "Servientrega",
        "QR al dominio oficial www.servientrega.com para rastrear guia -> legitimo",
        ["Servientrega", "Rastree su envio", "Escanee el codigo y escriba", "su numero de guia."],
        ("SV", "#2e7d32"),
        qr="https://www.servientrega.com/",
    ),
]


def qr_image(text: str, tiny: bool) -> Image.Image:
    """A QR of ``text``; ``tiny`` makes it unreadable in the whole screenshot (D-076)."""
    code = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode).to_image(scale=6)
    img = Image.frombytes("L", code.shape[::-1], bytes(memoryview(code)))
    if not tiny:
        return img.resize((240, 240), Image.Resampling.NEAREST)
    small = img.resize((48, 48), Image.Resampling.LANCZOS)
    return small.rotate(30, Image.Resampling.BICUBIC, expand=True, fillcolor=255)


def screenshot(sample: Sample) -> bytes:
    """The PNG of one sample: badge, text lines and its QR, if any."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(canvas)
    initials, colour = sample.badge
    draw.ellipse((40, 40, 140, 140), fill=colour)
    draw.text((90, 90), initials, fill="white", font=ImageFont.load_default(size=40), anchor="mm")
    font = ImageFont.load_default(size=26)
    for row, line in enumerate(sample.lines):
        draw.text((40, 180 + row * 44), line, fill="black", font=font)
    if sample.qr:
        qr = qr_image(sample.qr, sample.tiny_qr)
        canvas.paste(qr, (440, 780) if sample.tiny_qr else (180, 420))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def record(sample: Sample) -> dict[str, object]:
    """The eval line of one sample (specs/06)."""
    return {
        "id": sample.case_id,
        "tipo": "imagen",
        "pais": sample.pais,
        "fuente": f"evals/muestras/{sample.case_id}.png",
        "esperado": sample.esperado,
        "categoria": sample.categoria,
        "marca": sample.marca,
        "hipotesis": sample.hipotesis,
        "ambiguo": None,
        "rapido": False,
        "autor": "claude-code",
        "fecha": "2026-10-27",
        "origen": "sintetico",
        "qr_solo": sample.hipotesis.startswith(_QR_ONLY),
        "nota": "Captura sintética (tools/make_image_samples.py), logo dibujado y nombres"
        " ficticios. Texto: " + " ".join(sample.lines) + (f" QR: {sample.qr}" if sample.qr else ""),
    }


def main() -> int:
    """Write every sample PNG and print one eval line per case."""
    for sample in SAMPLES_SPEC:
        (SAMPLES / f"{sample.case_id}.png").write_bytes(screenshot(sample))
        sys.stdout.write(json.dumps(record(sample), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
