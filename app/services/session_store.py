"""In-memory session registry with TTL management.

No message content is ever stored here — only participant metadata and public keys.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.models.session import Participant, SessionState

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _short_code(session_id: str) -> str:
    """Derive a short human-readable session code from the session_id."""
    return session_id[:12].upper()


class SessionStore:
    def __init__(self, ttl_minutes: int = 30) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()
        self._ttl = timedelta(minutes=ttl_minutes)

    async def create_session(self, max_participants: int = 2) -> SessionState:
        """Create a new ephemeral session. Token generated with 256-bit entropy."""
        session_id = secrets.token_urlsafe(32)
        now = _now()
        state = SessionState(
            session_id=session_id,
            created_at=now,
            last_activity=now,
            max_participants=max_participants,
            participants={},
        )
        async with self._lock:
            self._sessions[session_id] = state
        logger.info("Session created: %s", session_id[:8])
        return state

    async def get_session(self, session_id: str) -> SessionState | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def add_participant(
        self, session_id: str, participant: Participant
    ) -> None:
        """Add a participant to a session. Raises ValueError if full or not found."""
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise ValueError(f"Session not found: {session_id[:8]}")
            if len(state.participants) >= state.max_participants:
                raise ValueError("Session is full")
            state.participants[participant.participant_id] = participant
            state.last_activity = _now()
        logger.info(
            "Participant joined session %s (%d/%d)",
            session_id[:8],
            len(state.participants),
            state.max_participants,
        )

    async def remove_participant(
        self, session_id: str, participant_id: str
    ) -> None:
        """Remove a participant. No-op if session or participant not found."""
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.participants.pop(participant_id, None)
            state.last_activity = _now()
        logger.info("Participant left session %s", session_id[:8])

    async def delete_session(self, session_id: str) -> None:
        """Immediately purge all in-memory state for a session."""
        async with self._lock:
            self._sessions.pop(session_id, None)
        logger.info("Session deleted: %s", session_id[:8])

    async def is_empty(self, session_id: str) -> bool:
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return True
            return len(state.participants) == 0

    async def sweep_expired(self) -> None:
        """Background task: purge sessions whose last_activity exceeds TTL."""
        async with self._lock:
            cutoff = _now() - self._ttl
            expired = [
                sid
                for sid, s in self._sessions.items()
                if s.last_activity < cutoff
            ]
            for sid in expired:
                del self._sessions[sid]
                logger.info("Session expired and purged: %s", sid[:8])

    async def run_sweep_loop(self, interval_seconds: int = 60) -> None:
        """Long-running background sweep loop. Run via asyncio.create_task()."""
        while True:
            await asyncio.sleep(interval_seconds)
            await self.sweep_expired()
