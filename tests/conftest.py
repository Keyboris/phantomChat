"""Shared pytest fixtures for Phantom Chat tests."""

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app, connection_manager
from app.services.rate_limiter import RateLimiter
from app.services.session_store import SessionStore

TEST_ORIGIN = "https://test.example.com"


def make_test_settings() -> Settings:
    return Settings(allowed_origin=TEST_ORIGIN)


@pytest.fixture
def settings() -> Settings:
    return make_test_settings()


@pytest_asyncio.fixture
async def session_store() -> AsyncGenerator[SessionStore, None]:
    """Isolated SessionStore per test — no shared state."""
    store = SessionStore(ttl_minutes=30)
    yield store


@pytest_asyncio.fixture
async def async_client(session_store: SessionStore) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient wired to the FastAPI app with mocked pgp_validator."""
    test_settings = make_test_settings()

    with patch("app.services.pgp_validator.validate_public_key_armor", return_value=True):
        app = create_app(settings=test_settings)
        # Override the session store with the isolated fixture instance
        import app.routes.session as session_route
        import app.routes.ws as ws_route
        limiter = RateLimiter()
        session_route.set_dependencies(session_store, limiter)
        ws_route.set_dependencies(session_store, limiter)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Origin": TEST_ORIGIN},
        ) as client:
            yield client


@pytest_asyncio.fixture
async def rate_limiter() -> RateLimiter:
    return RateLimiter()
