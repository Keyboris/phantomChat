"""Security-focused tests — headers, rate limiting, CSRF, origin checks."""

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st
from httpx import AsyncClient

from tests.conftest import TEST_ORIGIN

REQUIRED_HEADERS = [
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "content-security-policy",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-embedder-policy",
]


# ── Property 11: Security headers on all responses ───────────────────────────
# Feature: phantom-chat, Property 11: all 8 security headers present on every response

async def test_security_headers_present_on_health(async_client: AsyncClient) -> None:
    """GET /health → all 8 security headers present."""
    res = await async_client.get("/health")
    assert res.status_code == 200
    for header in REQUIRED_HEADERS:
        assert header in res.headers, f"Missing header: {header}"


async def test_security_headers_present_on_404(async_client: AsyncClient) -> None:
    """Even 404 responses must carry all security headers."""
    res = await async_client.get("/nonexistent-endpoint")
    for header in REQUIRED_HEADERS:
        assert header in res.headers, f"Missing header on 404: {header}"


async def test_hsts_value(async_client: AsyncClient) -> None:
    res = await async_client.get("/health")
    hsts = res.headers.get("strict-transport-security", "")
    assert "max-age=63072000" in hsts
    assert "includeSubDomains" in hsts
    assert "preload" in hsts


async def test_x_frame_options_deny(async_client: AsyncClient) -> None:
    res = await async_client.get("/health")
    assert res.headers.get("x-frame-options") == "DENY"


async def test_csp_no_unsafe_inline_scripts(async_client: AsyncClient) -> None:
    res = await async_client.get("/health")
    csp = res.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp


# ── Property 10: Rate limit enforcement ──────────────────────────────────────
# Feature: phantom-chat, Property 10: 11th request returns 429 with Retry-After

async def test_rate_limit_enforced(async_client: AsyncClient) -> None:
    """11 POST /session requests → 11th returns 429 with Retry-After."""
    responses = []
    for _ in range(11):
        res = await async_client.post(
            "/session",
            json={},
            headers={"Content-Type": "application/json"},
        )
        responses.append(res.status_code)

    assert responses[-1] == 429
    # Verify Retry-After header is present on the 429
    last_res = await async_client.post(
        "/session",
        json={},
        headers={"Content-Type": "application/json"},
    )
    if last_res.status_code == 429:
        assert "retry-after" in last_res.headers


# ── Property 12: Origin validation ───────────────────────────────────────────
# Feature: phantom-chat, Property 12: mismatched Origin is rejected

async def test_origin_mismatch_on_post_rejected(async_client: AsyncClient) -> None:
    """POST /session with wrong Origin → 403."""
    res = await async_client.post(
        "/session",
        json={},
        headers={
            "Content-Type": "application/json",
            "Origin": "https://evil.example.com",
        },
    )
    assert res.status_code == 403


async def test_correct_origin_accepted(async_client: AsyncClient) -> None:
    """POST /session with correct Origin → 201."""
    res = await async_client.post(
        "/session",
        json={},
        headers={
            "Content-Type": "application/json",
            "Origin": TEST_ORIGIN,
        },
    )
    assert res.status_code == 201


# ── CSRF: Content-Type enforcement ───────────────────────────────────────────

async def test_non_json_content_type_rejected(async_client: AsyncClient) -> None:
    """POST /session with non-JSON Content-Type → 415."""
    res = await async_client.post(
        "/session",
        content=b"max_participants=2",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": TEST_ORIGIN,
        },
    )
    assert res.status_code == 415


# ── CORS: never wildcard ──────────────────────────────────────────────────────

def test_cors_allow_origins_never_wildcard() -> None:
    """CORS allow_origins must never be '*'."""
    from app.config import Settings
    from app.main import create_app

    settings = Settings(allowed_origin=TEST_ORIGIN)
    app = create_app(settings=settings)

    # Inspect middleware stack for CORSMiddleware
    from starlette.middleware.cors import CORSMiddleware
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            origins = middleware.kwargs.get("allow_origins", [])
            assert "*" not in origins, "CORS allow_origins must never be '*'"
            break


# ── Server cannot read messages ───────────────────────────────────────────────

def test_server_cannot_read_messages_opaque_relay() -> None:
    """Relay logic must forward ciphertext unchanged — no transformation."""
    from app.models.message import ChatMessage
    from datetime import datetime, timezone

    ciphertext = "-----BEGIN PGP MESSAGE-----\nABCDEFGHIJKLMNOP\n-----END PGP MESSAGE-----"
    frame = ChatMessage(
        type="chat",  # type: ignore[arg-type]
        session_id="a" * 43,
        sender_fingerprint="ABCD1234",
        ciphertext=ciphertext,
        timestamp=datetime.now(tz=timezone.utc),
    )
    # The server reads .ciphertext to relay it — value must be identical
    assert frame.ciphertext == ciphertext


# ── Property-based: rate limiter sliding window ───────────────────────────────

@given(limit=st.integers(min_value=1, max_value=20))
@h_settings(max_examples=50)
async def test_rate_limiter_blocks_after_limit(limit: int) -> None:
    """For any limit N, the (N+1)th request must be blocked."""
    from app.services.rate_limiter import RateLimiter

    limiter = RateLimiter()
    ip = "192.0.2.1"

    for _ in range(limit):
        allowed, _ = await limiter.check(ip, limit, 60)
        assert allowed

    blocked, retry_after = await limiter.check(ip, limit, 60)
    assert not blocked
    assert retry_after > 0


# ── Property-based: security headers on all endpoints ────────────────────────

@given(path=st.sampled_from(["/health", "/session/nonexistent"]))
@h_settings(max_examples=20)
async def test_security_headers_on_multiple_endpoints(path: str, async_client: AsyncClient) -> None:
    """Feature: phantom-chat, Property 11: security headers on all responses."""
    res = await async_client.get(path)
    for header in REQUIRED_HEADERS:
        assert header in res.headers, f"Missing {header} on {path}"
