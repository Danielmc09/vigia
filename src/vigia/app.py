"""FastAPI application: HTTP entrypoint (application layer)."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from vigia.config import Settings, settings
from vigia.domain.analyst import AnalystResult, Evidence
from vigia.domain.case import Case, IncomingMessage
from vigia.domain.gate import Judge
from vigia.domain.member import Business
from vigia.domain.verdict import Verdict
from vigia.jev.gate import screen
from vigia.model.jev import make_judge
from vigia.services.ingest import normalize
from vigia.signals.detector import detect
from vigia.signals.knowledge import seeded_pack
from vigia.verdict.build import build_verdict

_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}

AnalystProvider = Callable[[Case], AnalystResult | None]
"""Port the endpoint calls for the analyst's read of a case; T-09 wires the real one."""


def _no_analyst(_: Case) -> AnalystResult | None:
    """Default analyst: none runs, the verdict stays degraded (no T-09 yet)."""
    return None


class CaseRequest(BaseModel):
    """Minimal body to analyze one text case outside any real channel (D-048).

    Attributes:
        country: ISO code of the country pack to judge it with.
        text: The message, as a member would forward it.
    """

    country: str
    text: str


def create_app(
    config: Settings, analyst: AnalystProvider = _no_analyst, judge: Judge | None = None
) -> FastAPI:
    """Build the API.

    Args:
        config: Settings for this process; ``mode == "prod"`` hides the API docs.
        analyst: Provider of the analyst's read of a case; defaults to none (degraded
            verdicts only), so tests and this endpoint never call a real model.
        judge: Jev for the manipulation gate; defaults to :func:`make_judge` (mock unless
            ``JEV_BACKEND=real`` with a key).

    Returns:
        The configured FastAPI application.
    """
    gate_judge = judge or make_judge(config)
    docs = config.mode != "prod"
    api = FastAPI(
        title="VIGIA",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    @api.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(_SECURITY_HEADERS)
        return response

    @api.get("/salud")
    async def health() -> dict[str, str]:
        """Liveness probe. Dependency checks arrive with observability (specs/11)."""
        return {"status": "ok"}

    @api.post("/caso")
    async def post_case(request: CaseRequest) -> Verdict:
        """Fuse a case into its verdict (specs/05), without any real channel or store.

        Args:
            request: Country and text of the case.

        Returns:
            The verdict, degraded unless ``analyst`` was given to :func:`create_app`.
        """
        pack = seeded_pack(request.country, config.knowledge_seed)
        if pack is None:
            raise HTTPException(status_code=422, detail=f"unknown country: {request.country!r}")
        msg = IncomingMessage(
            message_id=str(uuid4()),
            origin="local",
            sender="eval",
            kind="text",
            text=request.text,
            received_at=datetime.now(UTC),
        )
        # A throwaway business per request: nothing is stored, no tenant is touched (D-048).
        case = normalize(msg, Business(uuid4(), pack.country, pack.language))
        if case is None:
            raise HTTPException(status_code=422, detail="unsupported case")
        case = case.model_copy(update={"origin": "eval", "sender": None})
        # No chat here: one stateless text, so the gate sees no history (D-048).
        # ponytail: a real Jev call blocks the event loop up to its timeout; async on T-10.
        gate = screen(request.text, [], pack, gate_judge)
        evidence = Evidence(
            signals=detect(case, pack),
            analyst=analyst(case),
            manipulation_detected=gate.manipulation_detected,
        )
        return build_verdict(case, evidence, pack)

    return api


app = create_app(settings)
