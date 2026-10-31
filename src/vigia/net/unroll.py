"""Safe short-link unroller (D-099): hop by hop, never a private address, never the body."""

import ipaddress
import socket
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from vigia.domain.urls import Unrolled

MAX_HOPS = 3
BUDGET_S = 1.5  # total for the whole chain, not per hop
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_PORTS = {"http": 80, "https": 443}
_NO_HEAD = frozenset({405, 501})

Lookup = Callable[[str, int], list[str]]


class UnrollError(Exception):
    """The link could not be followed safely; the caller treats it as unresolved."""


def _addresses(host: str, port: int) -> list[str]:
    """Every address the host resolves to.

    ponytail: getaddrinfo has no timeout and a second lookup inside httpx can differ
    (DNS rebinding TOCTOU); pin the IP in the transport if this ever faces a hostile resolver.
    """
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # multicast counts as global for ipaddress; CGNAT, link-local and loopback do not
    return ip.is_global and not ip.is_multicast


def _check(url: str, lookup: Lookup) -> None:
    """Refuse a hop before it is requested: scheme, credentials, port and every address."""
    parts = urlsplit(url)
    if parts.scheme not in _PORTS or not parts.hostname or parts.username or parts.password:
        raise UnrollError("scheme or credentials not allowed")
    try:
        port = parts.port or _PORTS[parts.scheme]
    except ValueError as error:
        raise UnrollError("bad port") from error
    if port != _PORTS[parts.scheme]:
        raise UnrollError("port not allowed")
    try:
        addresses = lookup(parts.hostname, port)
    except (OSError, UnicodeError) as error:
        raise UnrollError("host does not resolve") from error
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UnrollError("host is not public")


def _probe(url: str, client: httpx.Client, budget: float) -> tuple[int, str | None]:
    """Status and Location of one hop; the body is never read."""
    if budget <= 0:
        raise UnrollError("time budget spent")
    client.cookies.clear()
    response = client.head(url, timeout=budget, follow_redirects=False)
    if response.status_code in _NO_HEAD:
        with client.stream("GET", url, timeout=budget, follow_redirects=False) as response:
            pass
    return response.status_code, response.headers.get("location")


def unroll(
    url: str,
    client: httpx.Client,
    lookup: Lookup = _addresses,
    clock: Callable[[], float] = time.monotonic,
) -> Unrolled:
    """Follow a link's redirects by hand, checking each hop before requesting it.

    Args:
        url: The link as it came in the message; without scheme it is tried over https.
        client: Client without redirects, cookies jar cleared on every hop.
        lookup: Host to addresses; injected so tests never touch DNS.
        clock: Monotonic clock; the budget is shared by every hop.

    Returns:
        The chain from the original link to where it lands.

    Raises:
        UnrollError: A hop is not public http(s), too many hops, or the budget ran out.
        httpx.HTTPError: The network failed.
    """
    chain = [url if "://" in url else "https://" + url]
    deadline = clock() + BUDGET_S
    while True:
        _check(chain[-1], lookup)
        status, location = _probe(chain[-1], client, deadline - clock())
        if status not in _REDIRECTS or not location:
            return Unrolled(chain=chain)
        if len(chain) > MAX_HOPS:
            raise UnrollError("too many hops")
        chain.append(urljoin(chain[-1], location))


def unroller() -> Callable[[str], Unrolled]:
    """A ready unroller over a fresh client that ignores proxies and env (D-099).

    Returns:
        A function from link to where it lands.
    """
    client = httpx.Client(trust_env=False, follow_redirects=False)
    return lambda url: unroll(url, client)
