"""QR signals of specs/04: URLs hidden in images go through the URL rules (rules, D-076)."""

from collections.abc import Sequence

import zxingcpp

from vigia.domain.country import CountryPack
from vigia.domain.image import Box, crop, open_image
from vigia.domain.signal import Signal
from vigia.domain.urls import extract_urls
from vigia.signals.urls import Resolver, is_official, url_signals


def decode_qr(image: bytes) -> list[str]:
    """Read the QR codes of an image with zxing-cpp (D-076).

    Args:
        image: Image bytes (the original or a crop).

    Returns:
        The text of each QR found; empty when there is none or the image does not decode.
    """
    try:
        img = open_image(image)
    except ValueError:
        return []
    found = zxingcpp.read_barcodes(
        img, formats=zxingcpp.BarcodeFormats(zxingcpp.BarcodeFormat.QRCode)
    )
    return [code.text for code in found if code.valid and code.text]


def qr_urls(image: bytes, boxes: Sequence[Box] = ()) -> list[str]:
    """The links of the QR codes of an image, or of the given boxes of it, each once.

    Args:
        image: Image bytes.
        boxes: SAM boxes to crop and decode; empty means the whole image.

    Returns:
        URLs in the order found; QR texts that hold no link (Wi-Fi, vCard) are dropped.
    """
    pieces = [crop(image, box) for box in boxes] if boxes else [image]
    texts = [text for piece in pieces for text in decode_qr(piece)]
    return list(dict.fromkeys(url for text in texts for url in extract_urls(text)))


def qr_signals(
    urls: Sequence[str], pack: CountryPack, resolver: Resolver | None = None
) -> list[Signal]:
    """URL signals for each QR link, plus a medium one when it leaves the official domains.

    Args:
        urls: ``Case.qr_urls``.
        pack: Country pack of the business.
        resolver: Short-link follower; ``None`` means no network.

    Returns:
        The signals, URL ones with ``origin="qr"``.
    """
    signals: list[Signal] = []
    for url in urls:
        found = url_signals(url, pack, resolver, origin="qr")
        signals += found
        if not is_official(found):
            signals.append(
                Signal(
                    type="qr_to_unofficial_domain",
                    weight="medium",
                    source="qr",
                    evidence={"url": url},
                )
            )
    return signals
