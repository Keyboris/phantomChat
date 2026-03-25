"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

from app.config import Settings, get_settings
from app.middleware.csrf import CSRFMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.routes import health, session, ws
from app.services.rate_limiter import RateLimiter
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)


class MJSStaticFiles(StaticFiles):
    """StaticFiles subclass that serves .mjs files with the correct MIME type.

    Browsers reject ES module imports unless Content-Type is text/javascript.
    Starlette's default MIME detection returns application/octet-stream for .mjs
    on systems where the MIME database doesn't include it.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith(".mjs") and not isinstance(response, NotModifiedResponse):
            response.headers["content-type"] = "text/javascript; charset=utf-8"
        return response


class ConnectionManager:
    """Tracks live WebSocket connections per session for broadcast."""

    def __init__(self) -> None:
        # session_id → list of active WebSocket connections
        self._connections: dict[str, list[WebSocket]] = {}

    def add(self, session_id: str, websocket: WebSocket) -> None:
        self._connections.setdefault(session_id, []).append(websocket)

    def remove(self, session_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(session_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self._connections.pop(session_id, None)

    async def broadcast(
        self, session_id: str, message: str, exclude: WebSocket | None = None
    ) -> None:
        for conn in list(self._connections.get(session_id, [])):
            if conn is exclude:
                continue
            try:
                await conn.send_text(message)
            except Exception:
                # Dead connection — will be cleaned up on disconnect
                pass


# Module-level singleton — imported by ws.py
connection_manager = ConnectionManager()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    store = SessionStore(ttl_minutes=settings.session_ttl_minutes)
    limiter = RateLimiter()

    # Inject singletons into route modules
    session.set_dependencies(store, limiter)
    ws.set_dependencies(store, limiter)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        sweep_task = asyncio.create_task(store.run_sweep_loop())
        logger.info("Phantom Chat started — TTL sweep active")
        try:
            yield
        finally:
            sweep_task.cancel()
            try:
                await sweep_task
            except asyncio.CancelledError:
                pass
            logger.info("Phantom Chat shutdown — sweep stopped")

    app = FastAPI(
        title="Phantom Chat",
        docs_url=None,   # disable Swagger UI in production
        redoc_url=None,
        lifespan=lifespan,
    )

    # Middleware order: outermost first (CORS → CSRF → SecurityHeaders)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.allowed_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(CSRFMiddleware, allowed_origin=settings.allowed_origin)
    app.add_middleware(SecurityHeadersMiddleware)

    # Routers
    app.include_router(health.router)
    app.include_router(session.router)
    app.include_router(ws.router)

    # Static frontend — served at /
    # MJSStaticFiles ensures .mjs files get text/javascript MIME type,
    # which browsers require to execute ES module imports.
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", MJSStaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


# ASGI entry point
app = create_app()
