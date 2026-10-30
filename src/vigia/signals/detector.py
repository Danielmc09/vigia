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
        resolver: Short-link follower; ``None`` means no network (evals, tests).

    Returns:
        Every signal, hard first. Never raises on network failures.
    """
    per_url = [url_signals(url, pack, resolver) for url in case.urls]
    qr = qr_signals(case.qr_urls, pack, resolver)
    unofficial = any(not is_official(found) for found in per_url) or any(
        s.type == "qr_to_unofficial_domain" for s in qr
    )
    signals = knowledge_signals(case, pack) + [s for found in per_url for s in found] + qr
    signals += pattern_signals(case.text or "", pack, unofficial)
    signals += address_book_signals(case, suppliers, pack)
    signals += sequence_signals(case, pack)
    return by_weight(signals)
