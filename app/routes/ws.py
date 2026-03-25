"""WebSocket handler — /ws/{session_id}.

Security invariants enforced here:
- Origin checked BEFORE accept() — close 1008 on mismatch
- Rate limit checked BEFORE accept()
- Payload size checked BEFORE schema validation
- Private key markers rejected immediately
- All cleanup in try/finally — session never left in partial state
- Server NEVER reads, logs, or stores message content
"""

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.config import Settings, get_settings
from app.models.message import (
    AckMessage,
    ChatMessage,
    IncomingFrame,
    KeyExchangeMessage,
    MessageType,
    TypingIndicator,
)
from app.models.session import Participant
from app.services.pgp_validator import validate_public_key_armor
from app.services.rate_limiter import RateLimiter
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()

_PRIVATE_KEY_MARKER = "BEGIN PGP PRIVATE KEY"
_incoming_adapter: TypeAdapter[IncomingFrame] = TypeAdapter(IncomingFrame)  # type: ignore[type-arg]

# Module-level singletons set by app factory
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
    global _session_store, _rate_limiter
    _session_store = store
    _rate_limiter = limiter


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    # Import here to avoid circular import at module load time
    from app.main import connection_manager

    # --- Pre-accept checks (no accept() yet) ---

    # 1. Origin check — supports comma-separated list in settings.allowed_origin
    origin = websocket.headers.get("origin", "")
    allowed_origins = {o.strip() for o in settings.allowed_origin.split(",")}
    if origin not in allowed_origins:
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    # 2. Rate limit
    client_ip = websocket.client.host if websocket.client else "unknown"
    allowed, retry_after = await limiter.check(
        client_ip, settings.rate_limit_per_minute, 60
    )
    if not allowed:
        await websocket.close(code=1008, reason="Rate limit exceeded")
        return

    # 3. Session must exist
    state = await store.get_session(session_id)
    if state is None:
        await websocket.close(code=1008, reason="Session not found")
        return

    # 4. Session must not be full
    if len(state.participants) >= state.max_participants:
        await websocket.close(code=1008, reason="Session full")
        return

    await websocket.accept()

    participant_id = secrets.token_urlsafe(16)
    connection_manager.add(session_id, websocket)

    try:
        await _handle_session(
            websocket, session_id, participant_id, settings, store, connection_manager
        )
    finally:
        connection_manager.remove(session_id, websocket)
        await store.remove_participant(session_id, participant_id)
        if await store.is_empty(session_id):
            await store.delete_session(session_id)
        logger.info("Participant disconnected from session %s", session_id[:8])


async def _handle_session(
    websocket: WebSocket,
    session_id: str,
    participant_id: str,
    settings: Settings,
    store: SessionStore,
    cm: object,
) -> None:
    from app.main import ConnectionManager
    assert isinstance(cm, ConnectionManager)

    # Broadcast presence: online to existing participants
    await cm.broadcast(
        session_id,
        json.dumps({"type": MessageType.PRESENCE, "status": "online"}),
        exclude=websocket,
    )

    # --- First frame MUST be KeyExchangeMessage ---
    raw = await _receive_checked(websocket, settings.max_payload_bytes)
    if raw is None:
        return

    key_frame = await _parse_key_exchange(websocket, raw)
    if key_frame is None:
        return

    if not validate_public_key_armor(key_frame.public_key_armor):
        await websocket.close(code=1008, reason="Invalid public key")
        return

    participant = Participant(
        participant_id=participant_id,
        public_key_armor=key_frame.public_key_armor,
        connected_at=datetime.now(tz=timezone.utc),
    )
    try:
        await store.add_participant(session_id, participant)
    except ValueError as exc:
        await websocket.close(code=1008, reason=str(exc))
        return

    # Relay this client's public key to all other participants
    await cm.broadcast(
        session_id,
        json.dumps({
            "type": MessageType.KEY_EXCHANGE,
            "public_key_armor": key_frame.public_key_armor,
        }),
        exclude=websocket,
    )

    # Send existing participants' public keys to the new joiner
    current_state = await store.get_session(session_id)
    if current_state:
        for pid, p in current_state.participants.items():
            if pid != participant_id and p.public_key_armor:
                await websocket.send_text(json.dumps({
                    "type": MessageType.KEY_EXCHANGE,
                    "public_key_armor": p.public_key_armor,
                }))

    logger.info("Key exchange complete in session %s", session_id[:8])

    # --- Main message loop ---
    while True:
        raw = await _receive_checked(websocket, settings.max_payload_bytes)
        if raw is None:
            break

        # Reject any frame containing private key material immediately
        if _PRIVATE_KEY_MARKER in raw:
            await websocket.close(code=1008, reason="Private key material detected")
            return

        frame = await _parse_incoming(websocket, raw)
        if frame is None:
            return

        if isinstance(frame, ChatMessage):
            # Relay ciphertext unchanged — server never reads content
            await cm.broadcast(
                session_id,
                json.dumps({
                    "type": MessageType.CHAT,
                    "session_id": frame.session_id,
                    "sender_fingerprint": frame.sender_fingerprint,
                    "ciphertext": frame.ciphertext,
                    "timestamp": frame.timestamp.isoformat(),
                }),
                exclude=websocket,
            )
            # ACK to sender
            ack = AckMessage(
                type=MessageType.ACK,
                message_id=secrets.token_urlsafe(8),
            )
            await websocket.send_text(ack.model_dump_json())
            logger.info("Message relayed in session %s", session_id[:8])

        elif isinstance(frame, TypingIndicator):
            await cm.broadcast(
                session_id,
                json.dumps({"type": MessageType.TYPING, "is_typing": frame.is_typing}),
                exclude=websocket,
            )


async def _receive_checked(
    websocket: WebSocket, max_bytes: int
) -> str | None:
    """Receive a text frame and enforce payload size and null-byte limits."""
    try:
        data = await websocket.receive_text()
    except WebSocketDisconnect:
        return None

    if len(data.encode()) > max_bytes:
        await websocket.close(code=1009, reason="Payload too large")
        return None

    if "\x00" in data:
        await websocket.close(code=1008, reason="Null bytes not allowed")
        return None

    return data


async def _parse_key_exchange(
    websocket: WebSocket, raw: str
) -> KeyExchangeMessage | None:
    """Parse and validate the first frame as a KeyExchangeMessage."""
    try:
        data = json.loads(raw)
        if data.get("type") != MessageType.KEY_EXCHANGE:
            await websocket.close(code=1008, reason="First frame must be key_exchange")
            return None
        return KeyExchangeMessage.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        await websocket.close(code=1007, reason="Invalid key exchange frame")
        return None


async def _parse_incoming(
    websocket: WebSocket, raw: str
) -> ChatMessage | TypingIndicator | None:
    """Parse and validate an incoming frame against the IncomingFrame union."""
    try:
        data = json.loads(raw)
        result = _incoming_adapter.validate_python(data)
        # KeyExchangeMessage mid-session is not accepted
        if isinstance(result, KeyExchangeMessage):
            await websocket.close(code=1008, reason="Unexpected key_exchange frame")
            return None
        return result  # type: ignore[return-value]
    except (json.JSONDecodeError, ValidationError):
        await websocket.close(code=1007, reason="Invalid frame")
        return None
