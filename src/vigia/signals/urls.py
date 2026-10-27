"""URL signals of specs/04: lookalikes, brand outside its domain, risky hosts (rules)."""

import ipaddress
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import SplitResult

from vigia.domain.country import Brand, CountryPack
from vigia.domain.signal import Signal, Weight
from vigia.domain.text import fold
from vigia.domain.urls import split_url

Resolver = Callable[[str], str]
"""Port that follows a short link to its final URL; raises on any network failure."""

SHORTENERS = frozenset({"bit.ly", "tinyurl.com", "cutt.ly", "rb.gy", "is.gd", "t.co"})
SUSPICIOUS_TLDS = frozenset(
    {"xyz", "top", "icu", "click", "link", "cfd", "shop", "site", "online", "buzz", "rest"}
)
# ponytail: hand-kept public suffixes instead of tldextract (D-030); add a country's
# second-level suffixes when its pack lands.
MULTI_LABEL_SUFFIXES = frozenset(
    {"com.co", "gov.co", "org.co", "net.co", "edu.co", "mil.co", "nom.co"}
    | {"com.mx", "gob.mx", "com.ar", "gob.ar", "com.br", "gov.br", "co.uk", "gov.uk"}
)
LOOKALIKE_MAX_EDITS = 2  # specs/04
LOOKALIKE_MIN_LABEL = 6  # D-031: shorter official names only match homographs
_CONFUSABLES = str.maketrans(
    {"0": "o", "1": "l", "а": "a", "е": "e", "о": "o", "р": "p"}
    | {"с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s"}
)
_TLS_PORTS = (None, 80, 443)


def registrable_domain(host: str) -> str:
    """The domain someone registered: ``a.b.claro.com.co`` → ``claro.com.co``.

    Args:
        host: Host, optionally with userinfo or port.

    Returns:
        The registrable domain in lowercase; an IP address is returned as is.
    """
    host = host.rsplit("@", 1)[-1].split(":")[0].strip(".").lower()
    if _is_ip(host):
        return host
    labels = host.split(".")
    size = 3 if ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES else 2
    return ".".join(labels[-size:])


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _skeleton(domain: str) -> str:
    """Domain as it looks: punycode decoded and confusables (0/o, 1/l, rn/m, Cyrillic) mapped."""
    labels = []
    for label in domain.split("."):
        try:
            labels.append(
                label.encode("ascii").decode("idna") if label.startswith("xn--") else label
            )
        except UnicodeError:
            labels.append(label)
    return ".".join(labels).lower().translate(_CONFUSABLES).replace("rn", "m").replace("vv", "w")


def _edits(a: str, b: str) -> int:
    """Levenshtein distance."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _lookalike(domain: str, pack: CountryPack) -> str | None:
    """The official domain this one imitates, if any."""
    seen = _skeleton(domain)
    for official in sorted(pack.official_domains()):
        edits = _edits(seen, _skeleton(official))
        if edits == 0 or (
            edits <= LOOKALIKE_MAX_EDITS and len(official.split(".")[0]) >= LOOKALIKE_MIN_LABEL
        ):
            return official
    return None


def _aliases(brand: Brand) -> set[str]:
    """How a brand shows up in a URL: its name and its official domain names, squashed."""
    names = [brand.name] + [d.split(".")[0] for d in brand.domains]
    return {re.sub(r"[^a-z0-9]", "", fold(n).lower()) for n in names}


def _brand_in(tokens: set[str], pack: CountryPack) -> Brand | None:
    """First pack brand whose name or official domain name is one of ``tokens``."""
    return next((b for b in pack.brands if _aliases(b) & tokens), None)


def _host_tokens(host: str) -> set[str]:
    """Whole tokens of a host (split on ``.``/``-``) plus each label without hyphens."""
    return set(re.split(r"[.-]", host)) | {label.replace("-", "") for label in host.split(".")}


def _path_tokens(path: str) -> set[str]:
    """Whole alphanumeric tokens of a URL path, accent-folded and lowercased."""
    return set(re.split(r"[^a-z0-9]+", fold(path).lower()))


def _host_signals(parts: SplitResult, domain: str, pack: CountryPack) -> list[Signal]:
    """Signals for a host outside the official domains."""
    found: list[tuple[str, Weight, dict[str, Any]]] = []
    if official := _lookalike(domain, pack):
        found.append(("lookalike_domain", "hard", {"official": official}))
    if brand := _brand_in(_host_tokens(parts.hostname or ""), pack):
        found.append(("brand_off_domain", "hard", {"brand": brand.name}))
    elif brand := _brand_in(_path_tokens(parts.path), pack):
        # D-035: a brand word in the path is often an ordinary word ("claro") or a payment
        # method (PSE); it adds up but never condemns alone.
        found.append(("brand_in_path", "medium", {"brand": brand.name}))
    if domain.rsplit(".", 1)[-1] in SUSPICIOUS_TLDS:
        found.append(("suspicious_tld", "medium", {}))
    if _is_ip(domain) or parts.port not in _TLS_PORTS:
        found.append(("ip_or_port", "medium", {}))
    return [Signal(type=t, weight=w, source="urls", evidence=e) for t, w, e in found]


def _shortener(url: str, pack: CountryPack, resolver: Resolver | None, origin: str) -> list[Signal]:
    """A short link is judged by where it goes; if it cannot be followed, it is medium."""
    unresolved = Signal(type="unresolved_shortener", weight="medium", source="urls")
    if resolver is None:
        return [unresolved]
    try:
        final = resolver(url)
    except Exception:  # noqa: BLE001 - any network failure is a light signal (specs/04)
        return [unresolved, Signal(type="could_not_verify", weight="light", source="urls")]
    resolved = url_signals(final, pack, None, origin)
    return [s.model_copy(update={"evidence": s.evidence | {"via": url}}) for s in resolved]


def url_signals(
    url: str, pack: CountryPack, resolver: Resolver | None = None, origin: str = "text"
) -> list[Signal]:
    """Signals for one URL found in text, a QR, a PDF or an email.

    Never raises: anything that cannot be checked becomes ``could_not_verify`` (light).

    Args:
        url: Cleaned URL, with or without scheme.
        pack: Country pack with the official domains.
        resolver: Follows short links; ``None`` leaves them unresolved (no network).
        origin: Where the URL came from (``text``, ``qr``...), kept as evidence.

    Returns:
        The signals, each with ``url``, ``domain`` and ``origin`` in its evidence.
    """
    base = {"url": url, "origin": origin}
    try:
        parts = split_url(url)
        domain = registrable_domain(parts.netloc)
        if domain in SHORTENERS:
            signals = _shortener(url, pack, resolver, origin)
        elif domain in pack.official_domains():
            signals = [Signal(type="official_domain", weight="light", source="urls")]
        else:
            signals = _host_signals(parts, domain, pack)
        if parts.scheme == "http":
            signals.append(Signal(type="http_without_tls", weight="light", source="urls"))
    except ValueError:  # malformed host or port
        return [Signal(type="could_not_verify", weight="light", source="urls", evidence=base)]
    base["domain"] = domain
    return [s.model_copy(update={"evidence": base | s.evidence}) for s in signals]


def is_official(signals: list[Signal]) -> bool:
    """True when the signals of a URL say it points to an official domain."""
    return any(s.type == "official_domain" for s in signals)
