"""URL extraction from free text, tolerant to how scammers write links (domain)."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ponytail: any dotted word with a letters-only TLD counts ("dijo.Bueno" too); signals filter
# false positives later. Swap for a public-suffix list if they hurt precision.
_URL = re.compile(
    r"(?<![@\w.-])(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,24}(?::\d+)?(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}'\""
_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_eid|igshid)$", re.IGNORECASE)


def _clean(raw: str) -> str:
    """Strip trailing punctuation and tracking params; lowercase scheme and host."""
    raw = raw.rstrip(_TRAILING)
    has_scheme = "://" in raw
    parts = urlsplit(raw if has_scheme else "//" + raw)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    kept = urlencode([(k, v) for k, v in query if not _TRACKING.match(k)])
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, kept, parts.fragment))
    return url if has_scheme else url.removeprefix("//")


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
    return list(dict.fromkeys(_clean(m.group()) for m in _URL.finditer(text)))
