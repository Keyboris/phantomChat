"""CSRF middleware — Origin validation and Content-Type enforcement."""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Methods that mutate state and require Origin + Content-Type checks
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# REST endpoints that require JSON Content-Type (excludes /ws paths)
_REST_PREFIX = "/session"


class CSRFMiddleware(BaseHTTPMiddleware):
    """Validates Origin header on state-mutating requests.

    Also rejects non-JSON Content-Type on REST endpoints (HTTP 415).
    WebSocket upgrade requests are exempt from Content-Type checks.
    """

    def __init__(self, app: object, allowed_origin: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        # Support comma-separated list of allowed origins for local dev
        # e.g. "http://localhost:8000,http://localhost:8080"
        self._allowed_origins = {o.strip() for o in allowed_origin.split(",")}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # WebSocket upgrades are handled separately in the WS route
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        if request.method in _MUTATING_METHODS:
            origin = request.headers.get("origin", "")
            if origin not in self._allowed_origins:
                return JSONResponse(
                    {"detail": "Forbidden: Origin mismatch"}, status_code=403
                )

            # Enforce JSON Content-Type on REST endpoints
            if request.url.path.startswith(_REST_PREFIX):
                content_type = request.headers.get("content-type", "")
                if not content_type.startswith("application/json"):
                    return JSONResponse(
                        {"detail": "Unsupported Media Type: expected application/json"},
                        status_code=415,
                    )

        return await call_next(request)
