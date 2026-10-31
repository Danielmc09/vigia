"""All deterministic signals of a case, in one call (rules)."""

from collections.abc import Sequence

from vigia.domain.case import Case
from vigia.domain.country import CountryPack
from vigia.domain.member import Supplier
from vigia.domain.signal import Signal, by_weight
from vigia.signals.address_book import address_book_signals
from vigia.signals.knowledge import knowledge_signals
from vigia.signals.patterns import pattern_signals
from vigia.signals.qr import qr_signals
from vigia.signals.sequence import sequence_signals
from vigia.signals.urls import Resolver, is_official, url_signals


def detect(
    case: Case,
    pack: CountryPack,
    suppliers: Sequence[Supplier] = (),
    resolver: Resolver | None = None,
) -> list[Signal]:
    """Run the campaign, URL, QR, text, address-book and sale-sequence rules over a case (specs/04).

    Args:
        case: Normalized case.
        pack: Country pack of the business, with its known campaigns attached.
        suppliers: Decrypted address book of the business.
        resolver: Short-link follower for text links; ``None`` means no network.

    Returns:
        Every signal, hard first. Never raises on network failures.
    """
    unrolled = [s for url in case.urls for s in url_signals(url, pack, resolver)]
    per_raw = [url_signals(url, pack) for url in case.urls]
    raw = [s for found in per_raw for s in found]
    qr = qr_signals(case.qr_urls, pack)  # a QR is never unrolled (D-098)
    # Patterns see the raw link; only verdict/build.py:_has_unofficial_url sees the unrolled one.
    # A shortener may cloak, answering the official site to the server and the phishing page to
    # the victim, so unrolling never softens a hard nor drops a medium beside other risk (D-098).
    unofficial = any(not is_official(found) for found in per_raw) or any(
        s.type == "qr_to_unofficial_domain" for s in qr
    )
    signals = knowledge_signals(case, pack) + unrolled + qr
    signals += pattern_signals(case.text or "", pack, unofficial)
    signals += address_book_signals(case, suppliers, pack)
    signals += sequence_signals(case, pack)
    # Signals of an unrolled destination carry "via"; any other medium or hard means risk.
    if any(s.weight != "light" and "via" not in s.evidence for s in signals):
        signals += [s for s in raw if s.type == "unresolved_shortener" and s not in signals]
    return by_weight(signals)
