"""URL extraction from free text, tolerant to how scammers write links (domain)."""

import re
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

# A scheme-less host is taken whole and kept only if its last label is a TLD: any two letters
# (ccTLD) or one of these gTLDs (D-039, revised). Taking it whole matters: cutting at a listed
# TLD turned chase.com.secure-login.ru into chase.com, an official domain (c-0054..c-0056).
SCHEMELESS_TLDS = frozenset(
    {"com", "net", "org", "info", "biz", "gov", "edu", "int", "example"}
    | {"app", "dev", "page", "xyz", "top", "zip", "mov", "site", "online", "shop", "store"}
    | {"live", "click", "link", "icu", "cfd", "buzz", "rest", "sbs", "vip", "club", "win"}
    | {"bid", "lat"}
)
_LABEL = r"[a-z0-9\u0370-\u03ff\u0400-\u04ff-]+"
_URL = re.compile(
    # With scheme: any TLD, any Unicode letter, so accented homographs reach lookalike (A19);
    # the lookahead forbids stopping before another label (no prefix of a longer host).
    r"(?<![@\w.-])(?:https?://(?:(?:[^\W_]|-)+\.)+[^\W\d_]{2,24}(?![^\W_]|[.-][^\W_])"
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


def _is_tld(label: str) -> bool:
    """Whether a scheme-less host's last label, as written, reads as a TLD and not prose.

    Sentence case ("Bancolombia", "Me", "Co") is a word starting a sentence after a period
    with no space, so it is rejected; all lower or all upper case is a TLD.
    """
    if not label.isalpha() or (label[0].isupper() and label[1:].islower()):
        return False
    return len(label) == 2 or label.lower() in SCHEMELESS_TLDS


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
    found = (
        m.group()
        for m in _URL.finditer(text)
        if not m["host"] or _is_tld(m["host"].rsplit(".", 1)[-1])
    )
    return list(dict.fromkeys(_clean(url) for url in found))
