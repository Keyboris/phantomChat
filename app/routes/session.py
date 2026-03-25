"""Session management endpoints: POST /session, DELETE /session/{session_id}."""

import logging
import re
from datetime import timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import Settings, get_settings
from app.models.session import SessionCreateRequest, SessionCreateResponse
from app.services.rate_limiter import RateLimiter
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level singletons injected via Depends in main.py
_session_store: SessionStore | None = None
_rate_limiter: RateLimiter | None = None


def get_session_store() -> SessionStore:
    if _session_store is None:
        raise RuntimeError("SessionStore not initialised")
    return _session_store


def get_rate_limiter() -> RateLimiter:
    if _rate_limiter is None:
        raise RuntimeError("RateLimiter not initialised")
    return _rate_limiter


def set_dependencies(store: SessionStore, limiter: RateLimiter) -> None:
    """Called from app factory to inject singletons."""
    global _session_store, _rate_limiter
    _session_store = store
    _rate_limiter = limiter


@router.post("/session", response_model=SessionCreateResponse, status_code=201)
async def create_session(
    request: Request,
    body: SessionCreateRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> SessionCreateResponse:
    """Create a new ephemeral session. Rate-limited to 10 req/min per IP."""
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = await limiter.check(
        client_ip, settings.rate_limit_per_minute, 60
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    state = await store.create_session(
        max_participants=body.max_participants
    )
    base = state.created_at if state.created_at.tzinfo is not None else state.created_at.replace(tzinfo=timezone.utc)
    expires_at = base + timedelta(minutes=settings.session_ttl_minutes)

    logger.info("Session created via REST: %s", state.session_id[:8])
    return SessionCreateResponse(
        session_id=state.session_id,
        session_code=state.session_id[:12].upper(),
        expires_at=expires_at,
    )


@router.delete("/session/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Explicitly delete a session and purge all in-memory state."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", session_id):
        raise HTTPException(status_code=422, detail="Invalid session_id format")

    existing = await store.get_session(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Session not found")

    await store.delete_session(session_id)
    return Response(status_code=204)
