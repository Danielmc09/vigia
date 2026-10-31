"""Short-link unroller (D-099): hop by hop, never a private address, never the body."""

import ipaddress
from collections.abc import Callable

import httpx
import pytest

from vigia.net.unroll import UnrollError, unroll

PUBLIC = "93.184.216.34"


def _lookup(host: str, port: int) -> list[str]:
    """Literal IPs resolve to themselves; any name to a public address."""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        return [PUBLIC]


def _client(
    redirects: dict[str, str], head_status: int = 200, seen: list[str] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(f"{request.method} {url}")
        if request.method == "HEAD" and head_status != 200:
            return httpx.Response(head_status)
        if url in redirects:
            return httpx.Response(302, headers={"location": redirects[url]})
        return httpx.Response(200, content=b"never read")

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_follows_the_chain_to_the_final_url() -> None:
    client = _client({"https://ma.sv/Ab12": "https://x.co/a", "https://x.co/a": "/b"})
    landed = unroll("https://ma.sv/Ab12", client, _lookup)
    assert landed.chain == ["https://ma.sv/Ab12", "https://x.co/a", "https://x.co/b"]
    assert (landed.hops, landed.final_url, landed.final_domain) == (2, "https://x.co/b", "x.co")
    assert landed.original_url == "https://ma.sv/Ab12"


def test_a_link_that_does_not_redirect_has_zero_hops() -> None:
    assert unroll("https://ma.sv/Ab12", _client({}), _lookup).hops == 0


def test_schemeless_link_is_followed_over_https() -> None:
    assert unroll("bit.ly/abc", _client({}), _lookup).chain == ["https://bit.ly/abc"]


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest",  # cloud metadata
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://100.64.0.1/",  # CGNAT
        "http://224.0.0.1/",  # multicast is global for ipaddress
        "http://[::ffff:127.0.0.1]/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_a_hop_to_a_non_public_address_is_refused(target: str) -> None:
    seen: list[str] = []
    client = _client({"https://bit.ly/x": target}, seen=seen)
    with pytest.raises(UnrollError):
        unroll("https://bit.ly/x", client, _lookup)
    assert seen == ["HEAD https://bit.ly/x"]  # the private hop is never requested


def test_a_name_resolving_to_a_private_address_is_refused() -> None:
    def lookup(host: str, port: int) -> list[str]:
        return [PUBLIC, "10.1.2.3"]  # any private answer is enough

    with pytest.raises(UnrollError):
        unroll("https://bit.ly/x", _client({}), lookup)


@pytest.mark.parametrize(
    "target",
    ["ftp://x.co/a", "https://x.co:8443/a", "https://user:pw@x.co/a", "file:///etc/passwd"],
)
def test_only_plain_http_and_https_on_standard_ports(target: str) -> None:
    with pytest.raises(UnrollError):
        unroll("https://bit.ly/x", _client({"https://bit.ly/x": target}), _lookup)


def test_more_than_three_hops_is_refused() -> None:
    chain = {f"https://x.co/{i}": f"https://x.co/{i + 1}" for i in range(4)}
    with pytest.raises(UnrollError):
        unroll("https://x.co/0", _client(chain), _lookup)


def test_three_hops_is_allowed() -> None:
    chain = {f"https://x.co/{i}": f"https://x.co/{i + 1}" for i in range(3)}
    assert unroll("https://x.co/0", _client(chain), _lookup).hops == 3


def test_head_not_allowed_falls_back_to_get() -> None:
    seen: list[str] = []
    client = _client({"https://ma.sv/Ab12": "https://x.co/a"}, head_status=405, seen=seen)
    assert unroll("https://ma.sv/Ab12", client, _lookup).final_url == "https://x.co/a"
    assert seen[:2] == ["HEAD https://ma.sv/Ab12", "GET https://ma.sv/Ab12"]


def _clock(step: float) -> Callable[[], float]:
    now = [0.0]

    def tick() -> float:
        now[0] += step
        return now[0]

    return tick


def test_the_time_budget_is_shared_by_every_hop() -> None:
    chain = {f"https://x.co/{i}": f"https://x.co/{i + 1}" for i in range(3)}
    with pytest.raises(UnrollError):
        unroll("https://x.co/0", _client(chain), _lookup, clock=_clock(0.5))


def test_cookies_are_never_sent_back() -> None:
    sent: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie"))
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b", "set-cookie": "s=1; Path=/"})
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    unroll("https://x.co/a", client, _lookup)
    assert sent == [None, None]


# --- wiring: the case pipeline unrolls before the rules; the model only reads the map ---

from datetime import UTC, datetime  # noqa: E402
from uuid import uuid4  # noqa: E402

from vigia.domain.analyst import AnalystResult, Evidence  # noqa: E402
from vigia.domain.case import Case  # noqa: E402
from vigia.domain.gate import GateResult  # noqa: E402
from vigia.domain.urls import Unrolled  # noqa: E402
from vigia.services import analyst_tools, process_case  # noqa: E402
from vigia.signals.country_pack import country_pack  # noqa: E402

CO = country_pack("CO")
assert CO is not None
GATE = GateResult(manipulation_detected=False, triage="investigate", source="mock")
SHORT = "https://bit.ly/Bq7Lm2x"
BANK = "https://www.bancolombia.com/personas"


def _case(text: str, urls: list[str]) -> Case:
    return Case(
        id=uuid4(),
        business_id=uuid4(),
        country="CO",
        origin="eval",
        kind="text",
        text=text,
        urls=urls,
        received_at=datetime(2026, 10, 31, tzinfo=UTC),
    )


def _legit(case: Case, evidence: Evidence, *_: object) -> AnalystResult:
    return AnalystResult(label="legit", confidence=0.9)


def test_classify_unrolls_only_short_links_and_can_absolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def follow(url: str) -> Unrolled:
        asked.append(url)
        return Unrolled(chain=[url, BANK])

    monkeypatch.setattr(process_case, "UNROLL", follow)
    case = _case("Tu extracto ya está disponible", [SHORT, "https://www.bancolombia.com/x"])

    verdict = process_case.classify(case, CO, GATE, _legit)

    assert asked == [SHORT]  # a plain link is never fetched
    assert verdict.label == "legit"


def test_classify_survives_an_unroller_that_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(url: str) -> Unrolled:
        raise UnrollError("down")

    monkeypatch.setattr(process_case, "UNROLL", broken)
    verdict = process_case.classify(_case("Extracto", [SHORT]), CO, GATE, _legit)

    assert verdict.label == "doubtful"
    assert "unresolved_shortener" in {s.type for s in verdict.signals}


def test_the_model_sees_the_unrolled_link_but_never_triggers_a_fetch() -> None:
    links = {SHORT: Unrolled(chain=[SHORT, BANK])}
    analyze = analyst_tools.analyze_url_tool(CO, links)

    assert analyze(SHORT, 1.0)["official"] is True
    unknown = analyze("https://bit.ly/Injected", 1.0)  # not in the map: no network, unresolved
    assert unknown["official"] is False
    assert "unresolved_shortener" in {s["type"] for s in unknown["signals"]}


def test_the_analyst_gets_the_map_the_rules_used(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Unrolled]] = []

    def spy(case: Case, evidence: Evidence, *_: object) -> AnalystResult:
        seen.append(evidence.links)
        return AnalystResult(label="legit", confidence=0.9)

    monkeypatch.setattr(process_case, "UNROLL", lambda url: Unrolled(chain=[url, BANK]))
    process_case.classify(_case("Extracto", [SHORT]), CO, GATE, spy)

    assert seen == [{SHORT: Unrolled(chain=[SHORT, BANK])}]


def test_only_production_follows_links_and_a_broken_client_means_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vigia import app

    assert app.startup_unroller("dev") is None  # tests and local runs never touch the network
    assert callable(app.startup_unroller("prod"))

    def broken() -> None:
        raise RuntimeError("no network stack")

    monkeypatch.setattr(app, "unroller", broken)
    assert app.startup_unroller("prod") is None
