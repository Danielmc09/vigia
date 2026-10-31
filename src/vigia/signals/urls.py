"""URL signals of specs/04: lookalikes, brand outside its domain, risky hosts (rules)."""

import ipaddress
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import SplitResult

from vigia.domain.country import Brand, CountryPack
from vigia.domain.signal import Signal, Weight
from vigia.domain.text import fold, squash
from vigia.domain.urls import Unrolled, split_url

Resolver = Callable[[str], Unrolled]
"""Port that follows a short link hop by hop (D-099); raises on any network failure."""

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
_SHORT_LABEL_MAX = 3  # D-100
_SHORT_PATH = re.compile(r"/[A-Za-z0-9]{4,12}/?")


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


def domain_name(domain: str) -> str:
    """A domain's first label, its name without suffix: ``claro.com.co`` → ``claro``.

    Args:
        domain: Registrable domain or host.

    Returns:
        The text before the first dot.
    """
    return domain.split(".")[0]


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
    for official in sorted(pack.official_domains() | pack.user_content_domains()):
        target = _skeleton(official)
        # Incident c-0187: also the name without its suffix (bancolornbia.com.co).
        edits = min(_edits(seen, target), _edits(domain_name(seen), domain_name(target)))
        if edits == 0 or (
            edits <= LOOKALIKE_MAX_EDITS and len(domain_name(official)) >= LOOKALIKE_MIN_LABEL
        ):
            return official
    return None


def aliases(brand: Brand) -> set[str]:
    """How a brand shows up in a URL or a query: its name and official domain names, squashed.

    Args:
        brand: A brand of the country pack.

    Returns:
        Folded lowercase names with anything but ``a-z0-9`` removed.
    """
    names = [brand.name] + [domain_name(d) for d in brand.domains]
    return {squash(n) for n in names}


def _brand_in(tokens: set[str], narrow: set[str], pack: CountryPack) -> Brand | None:
    """First pack brand whose name or official domain name is one of ``tokens``.

    An ambiguous brand (D-104) is also a common word ("meta.wikimedia.org"): it only
    counts among the ``narrow`` tokens, those of the registered domain or of an official
    domain spelled out in the host ("chase.com.secure-login.ru", D-105).
    """
    return next((b for b in pack.brands if aliases(b) & (narrow if b.ambiguous else tokens)), None)


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
    host = parts.hostname or ""
    spelled = {domain_name(d) for b in pack.brands for d in b.domains if f".{d}." in f".{host}."}
    tokens = _host_tokens(host)
    if brand := _brand_in(tokens, _host_tokens(domain) | spelled, pack):
        found.append(("brand_off_domain", "hard", {"brand": brand.name}))
    elif brand := _brand_in(tokens, tokens, pack):
        # D-105: an ambiguous brand as a foreign subdomain is the commonest phishing shape
        # ("claro.pagos-online.co") and also a common word ("meta.wikimedia.org"): medium.
        found.append(("brand_off_domain", "medium", {"brand": brand.name}))
    elif brand := _brand_in(_path_tokens(parts.path), set(), pack):
        # D-035: a brand word in the path is often an ordinary word ("claro") or a payment
        # method (PSE); it adds up but never condemns alone. An ambiguous brand never counts
        # here (D-105): "/meta/ofertas" is a word, not a brand.
        found.append(("brand_in_path", "medium", {"brand": brand.name}))
    if domain.rsplit(".", 1)[-1] in SUSPICIOUS_TLDS:
        found.append(("suspicious_tld", "medium", {}))
    if _is_ip(domain) or parts.port not in _TLS_PORTS:
        found.append(("ip_or_port", "medium", {}))
    return [Signal(type=t, weight=w, source="urls", evidence=e) for t, w, e in found]


def _looks_short(parts: SplitResult, domain: str) -> bool:
    """An unknown shortener by shape (D-100): ``ma.sv/QzZ72Mt``."""
    return (
        len(domain_name(domain)) <= _SHORT_LABEL_MAX
        and not _is_ip(domain)
        and not parts.query
        and _SHORT_PATH.fullmatch(parts.path) is not None
    )


def _follow(url: str, resolver: Resolver | None) -> Unrolled | None:
    """Where a link lands; ``None`` without a resolver or a redirect. Raises on network failure."""
    landed = resolver(url) if resolver else None
    return landed if landed and landed.hops else None


def _destination(url: str, landed: Unrolled, pack: CountryPack, origin: str) -> list[Signal]:
    """Signals of where a link landed, with the link and its chain as evidence."""
    trail = {"via": url, "chain": landed.chain}
    resolved = url_signals(landed.final_url, pack, None, origin)
    return [s.model_copy(update={"evidence": s.evidence | trail}) for s in resolved]


def _unrolled_by_shape(
    url: str, pack: CountryPack, resolver: Resolver | None, origin: str
) -> list[Signal] | None:
    """Destination signals of a short-looking link that redirects; ``None`` if it does not."""
    try:
        landed = _follow(url, resolver)
    except Exception:  # noqa: BLE001 - not a known shortener: no medium, fall back to the host
        return None
    return _destination(url, landed, pack, origin) if landed else None


def _shortener(url: str, pack: CountryPack, resolver: Resolver | None, origin: str) -> list[Signal]:
    """A short link is judged by where it goes; if it cannot be followed, it is medium."""
    unresolved = Signal(type="unresolved_shortener", weight="medium", source="urls")
    try:
        landed = _follow(url, resolver)
    except Exception:  # noqa: BLE001 - any network failure is a light signal (specs/04)
        return [unresolved, Signal(type="could_not_verify", weight="light", source="urls")]
    return _destination(url, landed, pack, origin) if landed else [unresolved]


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
        elif domain in pack.user_content_domains():
            signals = []  # D-105: a real host, but the page is anyone's
        elif domain in pack.official_domains():
            signals = [Signal(type="official_domain", weight="light", source="urls")]
        else:
            signals = _host_signals(parts, domain, pack)
            # Only a clean host may be judged by its destination: the attacker owns a dirty one.
            clean = all(s.weight == "light" for s in signals)
            if clean and _looks_short(parts, domain):
                # `or signals`: an empty destination (unknown domain, no features) falls back
                # to the host signals too, deliberately (D-100 anexo).
                signals = _unrolled_by_shape(url, pack, resolver, origin) or signals
        if parts.scheme == "http":
            signals.append(Signal(type="http_without_tls", weight="light", source="urls"))
    except ValueError:  # malformed host or port
        return [Signal(type="could_not_verify", weight="light", source="urls", evidence=base)]
    base["domain"] = domain
    return [s.model_copy(update={"evidence": base | s.evidence}) for s in signals]


def from_map(links: Mapping[str, Unrolled]) -> Resolver | None:
    """A resolver over links unrolled beforehand, so the rules and the model never fetch.

    Args:
        links: Unrolled short links of the case, by original URL.

    Returns:
        ``None`` when nothing was unrolled (offline); else a lookup where a link left out
        (failed or never asked) is ``could_not_verify``.
    """
    return links.__getitem__ if links else None


def needs_unrolling(url: str) -> bool:
    """True for a known shortener or a short-looking link: the only ones fetched (D-099, D-100)."""
    try:
        parts = split_url(url)
        domain = registrable_domain(parts.netloc)
        return domain in SHORTENERS or _looks_short(parts, domain)
    except ValueError:
        return False


def is_official(signals: list[Signal]) -> bool:
    """True when the signals of a URL say it points to an official domain."""
    return any(s.type == "official_domain" for s in signals)
