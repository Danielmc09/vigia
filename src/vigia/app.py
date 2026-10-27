"""FastAPI application: HTTP entrypoint (application layer)."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from vigia.config import Settings, settings

_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}


def create_app(config: Settings) -> FastAPI:
    """Build the API.

    Args:
        config: Settings for this process; ``mode == "prod"`` hides the API docs.

    Returns:
        The configured FastAPI application.
    """
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

    return api


app = create_app(settings)
