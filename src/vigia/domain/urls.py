"""URL extraction from free text, tolerant to how scammers write links (domain)."""

import re
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

# A scheme-less host is taken whole and kept only if its last label is a TLD: any two letters
# (ccTLD) or one of these gTLDs, never another word even with a path after it (D-039, revised).
# Taking it whole matters: cutting at a listed TLD turned chase.com.secure-login.ru into
# chase.com, an official domain (c-0054..c-0056).
SCHEMELESS_TLDS = frozenset(
    {"com", "net", "org", "info", "biz", "gov", "edu", "int", "example"}
    | {"app", "dev", "page", "xyz", "top", "zip", "mov", "site", "online", "shop", "store"}
    | {"live", "click", "link", "icu", "cfd", "buzz", "rest", "sbs", "vip", "club", "win"}
    | {"bid", "lat"}
    # Abused by phishing kits and not Spanish words: pagos-dian.support (c-0232).
    | {"support", "help", "services", "center", "cyou", "bond"}
)
_TLD_MAX = 24
_LABEL = r"[a-z0-9\u0370-\u03ff\u0400-\u04ff-]+"
_URL = re.compile(
    # With scheme: any TLD, any Unicode letter, so accented homographs reach lookalike (A19);
    # the lookahead forbids stopping before another label (no prefix of a longer host).
    rf"(?<![@\w.-])(?:https?://(?:(?:[^\W_]|-)+\.)+[^\W\d_]{{2,{_TLD_MAX}}}(?![^\W_]|[.-][^\W_])"
    r"|https?://\d{1,3}(?:\.\d{1,3}){3}"
    # Without scheme: ASCII, Greek and Cyrillic labels, as many as there are (greedy).
    rf"|(?P<host>{_LABEL}(?:\.{_LABEL})+))"
    r"(?::\d+)?(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}'\""
_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_eid|igshid)$", re.IGNORECASE)


def split_url(url: str) -> SplitResult:
    """Split a URL that may come without scheme (``bit.ly/x``) into its parts.

    Args:
        url: URL as extracted from a message.

    Returns:
        The parts; ``netloc`` is the host (and port) even without scheme.
    """
    return urlsplit(url if "://" in url else "//" + url)


def _clean(raw: str) -> str:
    """Strip trailing punctuation and tracking params; lowercase scheme and host."""
    raw = raw.rstrip(_TRAILING)
    has_scheme = "://" in raw
    parts = split_url(raw)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    kept = urlencode([(k, v) for k, v in query if not _TRACKING.match(k)])
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, kept, parts.fragment))
    return url if has_scheme else url.removeprefix("//")


def _is_tld(label: str, evidence: bool) -> bool:
    """Whether a scheme-less host's last label, as written, reads as a TLD and not prose.

    Sentence case ("Bancolombia", "Su", "Help") is a word starting a sentence after a period
    with no space, so it is prose unless a path or port follows (``Bit.Ly/3xQ``, c-0233).
    A label outside the list is prose even with a path: ``bancolombia.ahorros/corriente``
    would otherwise give a hard signal on a legitimate message (c-0237..c-0240).
    """
    if not label.isalpha():
        return False
    known = len(label) == 2 or label.lower() in SCHEMELESS_TLDS
    if label[0].isupper() and label[1:].islower():
        return evidence and known
    return known


def _is_url(m: re.Match[str]) -> bool:
    """Whether a ``_URL`` match is a link: a scheme, or a host whose last label is a TLD.

    A path or port after the host is the evidence ``_is_tld`` weighs.
    """
    host = m["host"]
    return not host or _is_tld(host.rsplit(".", 1)[-1], evidence=m.end() > m.end("host"))


def defang(host: str) -> str:
    """A host readable but not clickable: dots written as ``[.]``.

    Args:
        host: Domain or host name.

    Returns:
        The disarmed host.
    """
    return host.replace(".", "[.]")


def extract_urls(text: str) -> list[str]:
    """Find URLs in a message, with or without scheme, cleaned and deduplicated.

    Hosts and schemes are lowercased (paths keep their case: short links are
    case-sensitive), tracking parameters are dropped, trailing punctuation is removed and
    email addresses are not URLs.

    Args:
        text: Message body.

    Returns:
        URLs in order of first appearance.

    Example:
        >>> extract_urls("Reclama en bit.ly/3xQnq.")
        ['bit.ly/3xQnq']
    """
    found = (m.group() for m in _URL.finditer(text) if _is_url(m))
    return list(dict.fromkeys(_clean(url) for url in found))


class Unrolled(BaseModel, frozen=True):
    """Where a short link lands after following its redirects by hand (D-099).

    Attributes:
        chain: Every URL visited, the original first and the final one last.
    """

    chain: list[str]

    @property
    def original_url(self) -> str:
        """The link as followed: as it came, with ``https://`` added when it had no scheme."""
        return self.chain[0]

    @property
    def final_url(self) -> str:
        """Where the last hop landed."""
        return self.chain[-1]

    @property
    def hops(self) -> int:
        """How many redirects were followed; 0 means the link does not redirect."""
        return len(self.chain) - 1

    @property
    def final_domain(self) -> str:
        """Host of the final URL, without port."""
        return split_url(self.final_url).hostname or ""


def disarm_urls(text: str) -> str:
    """Replace every URL in a text with its host, dots written as ``[.]``.

    The same URLs ``extract_urls`` finds; path and query go, since they may carry a
    victim's id (``?doc=123``), and the host stays readable but not clickable.

    Args:
        text: Message body.

    Returns:
        The text with each URL disarmed and the rest untouched.

    Example:
        >>> disarm_urls("Pague en https://simit-pagos.click/pago?doc=1.")
        'Pague en simit-pagos[.]click.'
    """

    def disarm(m: re.Match[str]) -> str:
        if not _is_url(m):
            return m.group()
        raw = m.group()
        url = raw.rstrip(_TRAILING)
        return defang(split_url(url).hostname or url) + raw[len(url) :]

    return _URL.sub(disarm, text)
