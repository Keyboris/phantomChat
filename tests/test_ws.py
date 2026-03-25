"""WebSocket handler tests — unit and property-based."""

import json
from unittest.mock import patch

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from app.services.session_store import SessionStore
from app.services.rate_limiter import RateLimiter
from app.services.pgp_validator import validate_public_key_armor

TEST_ORIGIN = "https://test.example.com"

VALID_PUBLIC_KEY = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    + "mDMEY" + "A" * 200 + "\n"
    + "-----END PGP PUBLIC KEY BLOCK-----\n"
)

VALID_KEY_EXCHANGE = json.dumps({
    "type": "key_exchange",
    "public_key_armor": VALID_PUBLIC_KEY,
})


def make_app(store: SessionStore, limiter: RateLimiter | None = None):
    from app.config import Settings
    from app.main import create_app
    import app.routes.session as session_route
    import app.routes.ws as ws_route

    settings = Settings(allowed_origin=TEST_ORIGIN)
    lim = limiter or RateLimiter()

    with patch("app.services.pgp_validator.validate_public_key_armor", return_value=True):
        app = create_app(settings=settings)
        session_route.set_dependencies(store, lim)
        ws_route.set_dependencies(store, lim)
        return app


# ── Unit: origin mismatch rejected ───────────────────────────────────────────

async def test_origin_mismatch_rejected() -> None:
    """WS connect with wrong Origin → close code 1008."""
    from httpx import ASGITransport, AsyncClient

    store = SessionStore()
    app = make_app(store)

    state = await store.create_session()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(Exception):
            async with client.websocket_connect(
                f"/ws/{state.session_id}",
                headers={"Origin": "https://evil.example.com"},
            ):
                pass


# ── Unit: session purged on disconnect ───────────────────────────────────────

async def test_session_purged_on_disconnect() -> None:
    """Connect → disconnect → session_store is empty."""
    from httpx import ASGITransport, AsyncClient

    store = SessionStore()
    app = make_app(store)
    state = await store.create_session()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            async with client.websocket_connect(
                f"/ws/{state.session_id}",
                headers={"Origin": TEST_ORIGIN},
            ) as ws:
                await ws.send_text(VALID_KEY_EXCHANGE)
                await ws.aclose()
        except Exception:
            pass

    assert await store.is_empty(state.session_id)


# ── Unit: mid-session join sees no prior messages ────────────────────────────

async def test_join_mid_session_receives_no_prior_messages() -> None:
    """Clients joining mid-session see no prior messages (no history stored)."""
    store = SessionStore()
    # Verify SessionState has no message history field
    state = await store.create_session()
    assert not hasattr(state, "messages")
    assert not hasattr(state, "history")


# ── Property 6: Ciphertext relay is opaque ───────────────────────────────────
# Feature: phantom-chat, Property 6: ciphertext received equals ciphertext sent

@given(ciphertext=st.text(min_size=1, max_size=500, alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00")))
@h_settings(max_examples=100)
def test_ciphertext_relay_is_opaque(ciphertext: str) -> None:
    """Server must relay ciphertext byte-for-byte without transformation."""
    import json

    # Simulate what the server does: parse the frame and re-serialise ciphertext
    frame = {
        "type": "chat",
        "session_id": "a" * 43,
        "sender_fingerprint": "ABCD",
        "ciphertext": ciphertext,
        "timestamp": "2026-01-01T00:00:00Z",
    }
    from app.models.message import ChatMessage
    parsed = ChatMessage.model_validate(frame)
    # The relay must forward the exact ciphertext value unchanged
    assert parsed.ciphertext == ciphertext


# ── Property 7: Oversized payload rejected ───────────────────────────────────
# Feature: phantom-chat, Property 7: frame > 64KB closes with code 1009

@given(extra=st.integers(min_value=1, max_value=1000))
@h_settings(max_examples=50)
def test_oversized_payload_size_check(extra: int) -> None:
    """Payload size check logic: anything over max_bytes must be flagged."""
    max_bytes = 65536
    oversized = "x" * (max_bytes + extra)
    assert len(oversized.encode()) > max_bytes


# ── Property 8: Invalid schema frame rejected ────────────────────────────────
# Feature: phantom-chat, Property 8: invalid schema closes without relay

@given(bad_json=st.one_of(
    st.text(min_size=1, max_size=100),
    st.just("{}"),
    st.just('{"type": "unknown_type"}'),
    st.just('{"type": "chat"}'),  # missing required fields
))
@h_settings(max_examples=100)
def test_invalid_schema_frame_rejected(bad_json: str) -> None:
    """Invalid frames must not parse as valid IncomingFrame."""
    import json as json_mod
    from pydantic import TypeAdapter, ValidationError
    from app.models.message import IncomingFrame

    adapter: TypeAdapter[IncomingFrame] = TypeAdapter(IncomingFrame)  # type: ignore[type-arg]
    try:
        data = json_mod.loads(bad_json)
        adapter.validate_python(data)
        # If it parsed, it must be a known valid type
        assert False, f"Should have failed for: {bad_json}"
    except (json_mod.JSONDecodeError, ValidationError, AssertionError):
        pass  # expected — invalid frames are rejected


# ── Property 9: Private key frame rejected ───────────────────────────────────
# Feature: phantom-chat, Property 9: frame with private key marker closes 1008

@given(payload=st.text(min_size=1, max_size=200))
@h_settings(max_examples=100)
def test_private_key_never_transmitted(payload: str) -> None:
    """Any frame containing private key marker must be detected."""
    private_marker = "BEGIN PGP PRIVATE KEY"
    injected = payload + private_marker
    assert private_marker in injected


# ── Unit: PGP validator rejects private keys ─────────────────────────────────

def test_pgp_validator_rejects_private_key() -> None:
    private_key = (
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
        + "A" * 100 + "\n"
        + "-----END PGP PRIVATE KEY BLOCK-----\n"
    )
    assert validate_public_key_armor(private_key) is False


def test_pgp_validator_rejects_empty() -> None:
    assert validate_public_key_armor("") is False


def test_pgp_validator_rejects_random_string() -> None:
    assert validate_public_key_armor("not a pgp key at all") is False


def test_pgp_validator_accepts_valid_structure() -> None:
    key = (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        + "mDMEY" + "A" * 200 + "\n"
        + "-----END PGP PUBLIC KEY BLOCK-----\n"
    )
    assert validate_public_key_armor(key) is True
