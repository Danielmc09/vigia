"""QR signals of specs/04: URLs hidden in images go through the URL rules (rules)."""

from collections.abc import Callable

from vigia.domain.country import CountryPack
from vigia.domain.signal import Signal
from vigia.signals.urls import is_official, url_signals

Decoder = Callable[[bytes], list[str]]
"""Turns an image (original or a SAM crop) into the texts of the QR codes in it."""


def decode_qr(image: bytes) -> list[str]:
    """Read the QR codes of an image.

    Args:
        image: Image bytes.

    Returns:
        Nothing yet: no QR decoder is installed (D-033); T-12 brings one.
    """
    # ponytail: no decoder dependency allowed today; every image reads as "no QR".
    return []


def qr_signals(image: bytes, pack: CountryPack, decode: Decoder = decode_qr) -> list[Signal]:
    """URL signals for each QR in an image, plus a medium one when it leaves the official domains.

    Args:
        image: Image bytes.
        pack: Country pack of the business.
        decode: QR decoder; injectable so the rule is tested without one.

    Returns:
        The signals, URL ones with ``origin="qr"``.
    """
    signals: list[Signal] = []
    for url in decode(image):
        found = url_signals(url, pack, origin="qr")
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
