"""Session lifecycle tests — unit and property-based."""

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st
from httpx import AsyncClient

from app.services.session_store import SessionStore
from app.models.session import Participant
from datetime import datetime, timezone, timedelta


# ── Unit tests ────────────────────────────────────────────────────────────────

async def test_create_session_returns_valid_response(async_client: AsyncClient) -> None:
    res = await async_client.post(
        "/session",
        json={},
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 201
    data = res.json()
    assert "session_id" in data
    assert "session_code" in data
    assert "expires_at" in data
    assert len(data["session_id"]) == 43


async def test_delete_session_returns_204(async_client: AsyncClient, session_store: SessionStore) -> None:
    state = await session_store.create_session()
    res = await async_client.delete(f"/session/{state.session_id}")
    assert res.status_code == 204
    assert await session_store.get_session(state.session_id) is None


async def test_delete_unknown_session_returns_404(async_client: AsyncClient) -> None:
    fake_id = "a" * 43
    res = await async_client.delete(f"/session/{fake_id}")
    assert res.status_code == 404


async def test_delete_invalid_session_id_format_returns_422(async_client: AsyncClient) -> None:
    res = await async_client.delete("/session/not-a-valid-id")
    assert res.status_code == 422


# ── Property 1: Session token uniqueness ─────────────────────────────────────
# Feature: phantom-chat, Property 1: two create_session() calls return distinct tokens

@pytest.mark.asyncio
@given(n=st.integers(min_value=2, max_value=20))
@h_settings(max_examples=100)
async def test_session_tokens_are_unique(n: int) -> None:
    store = SessionStore()
    sessions = [await store.create_session() for _ in range(n)]
    ids = [s.session_id for s in sessions]
    assert len(ids) == len(set(ids)), "Session IDs must be unique"


# ── Property 2: Session create-delete round trip ──────────────────────────────
# Feature: phantom-chat, Property 2: create → get → delete → get returns None

@pytest.mark.asyncio
@given(max_p=st.integers(min_value=2, max_value=10))
@h_settings(max_examples=100)
async def test_session_create_delete_roundtrip(max_p: int) -> None:
    store = SessionStore()
    state = await store.create_session(max_participants=max_p)
    assert await store.get_session(state.session_id) is not None
    await store.delete_session(state.session_id)
    assert await store.get_session(state.session_id) is None


# ── Property 3: Session full rejection ───────────────────────────────────────
# Feature: phantom-chat, Property 3: adding (N+1)th participant raises ValueError

@pytest.mark.asyncio
@given(max_p=st.integers(min_value=2, max_value=5))
@h_settings(max_examples=50)
async def test_session_full_rejection(max_p: int) -> None:
    store = SessionStore()
    state = await store.create_session(max_participants=max_p)

    for i in range(max_p):
        p = Participant(
            participant_id=f"p{i}",
            connected_at=datetime.now(tz=timezone.utc),
        )
        await store.add_participant(state.session_id, p)

    extra = Participant(
        participant_id="overflow",
        connected_at=datetime.now(tz=timezone.utc),
    )
    with pytest.raises(ValueError, match="full"):
        await store.add_participant(state.session_id, extra)


# ── Property 4: Session purged when empty ────────────────────────────────────
# Feature: phantom-chat, Property 4: remove all participants → session deleted

@pytest.mark.asyncio
@given(max_p=st.integers(min_value=2, max_value=5))
@h_settings(max_examples=50)
async def test_session_purged_when_empty(max_p: int) -> None:
    store = SessionStore()
    state = await store.create_session(max_participants=max_p)

    participant_ids = []
    for i in range(max_p):
        pid = f"p{i}"
        participant_ids.append(pid)
        p = Participant(
            participant_id=pid,
            connected_at=datetime.now(tz=timezone.utc),
        )
        await store.add_participant(state.session_id, p)

    for pid in participant_ids:
        await store.remove_participant(state.session_id, pid)

    assert await store.is_empty(state.session_id)


# ── Property 5: Session expiry purge ─────────────────────────────────────────
# Feature: phantom-chat, Property 5: expired session is purged by sweep

@pytest.mark.asyncio
async def test_session_expires_after_ttl() -> None:
    store = SessionStore(ttl_minutes=30)
    state = await store.create_session()

    # Manually backdate last_activity beyond TTL
    async with store._lock:
        store._sessions[state.session_id].last_activity = (
            datetime.now(tz=timezone.utc) - timedelta(minutes=31)
        )

    await store.sweep_expired()
    assert await store.get_session(state.session_id) is None
