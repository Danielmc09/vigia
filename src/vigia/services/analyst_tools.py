"""The analyst's tools bound to one case: pack, address book, web search, crop (application).

Each tool is a closure over the case's data; :mod:`vigia.analyst.tools` only knows names,
arguments and refusals. What a tool returns goes to the model provider, so it carries the
least it can (D-064).
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from vigia.analyst.tools import ACTION_TOOLS, Tool, refuse_action, reject
from vigia.domain.analyst import ModelTurn
from vigia.domain.country import CountryPack
from vigia.domain.image import QR_PROMPT, Segmenter, largest
from vigia.domain.member import Supplier
from vigia.domain.rejection import Rejection
from vigia.domain.signal import Signal
from vigia.domain.text import fold, mail_domain, only_digits, squash
from vigia.domain.urls import Unrolled, split_url
from vigia.signals.qr import qr_urls
from vigia.signals.urls import (
    aliases,
    domain_name,
    from_map,
    is_official,
    registrable_domain,
    url_signals,
)

MAX_SEARCHES = 2  # specs/03: web searches per case
MAX_MATCHES = 5  # D-064
MIN_PARTIAL = 4  # D-064: shorter queries only match a whole name
ACCOUNT_END = 4  # D-064
CROP_PROMPTS = ("logo", QR_PROMPT, "button")
"""What ``crop`` may ask SAM for (specs/03): a closed list, never the model's free text."""


class Searcher(Protocol):
    """Anything that can run one web search (``SparkClient`` in production)."""

    def web_search(self, query: str, timeout_s: float) -> ModelTurn | Rejection:
        """Search once; see :meth:`vigia.model.spark.SparkClient.web_search`."""


def _matches(needle: str, names: set[str]) -> bool:
    return bool(needle) and any(
        needle == n or (len(needle) >= MIN_PARTIAL and needle in n) for n in names
    )


def analyze_url_tool(pack: CountryPack, links: Mapping[str, Unrolled]) -> Tool:
    """``analyze_url`` over the short links the pipeline already unrolled (D-101).

    The model picks the URL, so it never reaches the network: a short link outside
    ``links`` stays unresolved, whatever the message told the model to ask for.

    Args:
        pack: Country pack of the business.
        links: Unrolled short links of the case, by original URL.

    Returns:
        The tool.
    """
    resolver = from_map(links)

    def analyze_url(url: str, _: float) -> dict[str, Any]:
        signals = url_signals(url, pack, resolver)
        if not signals:
            # Nothing fired, but "not official" is still news for the model (D-063).
            signals = [Signal(type="unknown_domain", weight="light", source="urls")]
        dumped = [s.model_dump(mode="json") for s in signals]
        return {"url": url, "official": is_official(signals), "signals": dumped}

    return analyze_url


def _needle(query: str) -> str:
    """What to compare with brand aliases: a domain's first label, or the squashed name."""
    text = fold(query).strip().lower()
    if "." in text and " " not in text:
        try:
            text = domain_name(registrable_domain(split_url(text).netloc))
        except ValueError:
            pass
    return squash(text)


def _check_country(pack: CountryPack) -> Tool:
    def check_country(query: str, _: float) -> dict[str, Any]:
        needle = _needle(query)
        found = [b for b in pack.brands if _matches(needle, aliases(b))]
        brands = [
            b.model_dump(include={"name", "domains", "official_channel", "kind"})
            for b in found[:MAX_MATCHES]
        ]
        return {"country": pack.country, "brands": brands}

    return check_country


def _known(supplier: Supplier) -> dict[str, str]:
    """What the model may see of a supplier (D-064): no mailbox, no full account."""
    return {
        "name": supplier.name,
        "email_domain": mail_domain(supplier.email),
        "account_end": only_digits(supplier.account or "")[-ACCOUNT_END:],
    }


def _check_address_book(suppliers: Sequence[Supplier]) -> Tool:
    def check_address_book(query: str, _: float) -> dict[str, Any]:
        needle, digits = squash(query), only_digits(query)
        found = [
            s
            for s in suppliers
            if _matches(needle, {squash(s.name), squash(s.email or "")})
            or (len(digits) >= ACCOUNT_END and only_digits(s.account or "").endswith(digits))
        ]
        return {"suppliers": [_known(s) for s in found[:MAX_MATCHES]]}

    return check_address_book


def _web_search(search: Searcher) -> Tool:
    done: list[str] = []

    def web_search(query: str, timeout_s: float) -> dict[str, Any]:
        if len(done) >= MAX_SEARCHES:
            return reject("LIMIT_REACHED", f"at most {MAX_SEARCHES} web searches per case")
        done.append(query)
        turn = search.web_search(query, timeout_s)
        if isinstance(turn, Rejection):
            return turn.model_dump()
        return {"answer": turn.content or "", "usd": turn.usd}

    return web_search


def crop_tool(image: bytes, segment: Segmenter) -> Tool:
    """``crop`` (specs/03 ``recortar``): SAM boxes of a prompt in the case image.

    A ``qr code`` crop is also decoded, so a QR too small for the reading step reaches the
    model as links it can pass to ``analyze_url``.

    Args:
        image: The case's image (already checked).
        segment: SAM.

    Returns:
        The bound tool.
    """

    def crop(prompt: str, _: float) -> dict[str, Any]:
        if prompt not in CROP_PROMPTS:
            return reject("BAD_ARGUMENTS", f"prompt must be one of {', '.join(CROP_PROMPTS)}")
        found = segment(image, prompt)
        if isinstance(found, Rejection):
            return found.model_dump()
        boxes = largest(found.boxes, MAX_MATCHES)
        links = qr_urls(image, boxes) if prompt == QR_PROMPT and boxes else []
        dumped = [b.model_dump() for b in boxes]
        return {"prompt": prompt, "boxes": dumped, "qr_urls": links, "usd": found.usd}

    return crop


def toolbox(
    pack: CountryPack,
    suppliers: Sequence[Supplier],
    search: Searcher | None,
    crop: Tool | None = None,
) -> dict[str, Tool]:
    """The analyst's tools bound to one case's country pack and address book (specs/03).

    Args:
        pack: Country pack of the business.
        suppliers: Its decrypted address book.
        search: Web search, only when the gate said ``investigate``; ``None`` leaves the
            tool out. Capped at ``MAX_SEARCHES`` per toolbox (one toolbox per case).
        crop: :func:`crop_tool` over the case image; ``None`` (no image, no SAM) leaves it out.

    Returns:
        Tool name to function; action tools are bound to a refusal.
    """
    tools = {
        "analyze_url": analyze_url_tool(pack, {}),  # spark_analyst rebinds it to the case's links
        "check_country": _check_country(pack),
        "check_address_book": _check_address_book(suppliers),
    }
    if search is not None:
        tools["web_search"] = _web_search(search)
    if crop is not None:
        tools["crop"] = crop
    return tools | dict.fromkeys(ACTION_TOOLS, refuse_action)
