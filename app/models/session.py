"""Pydantic models for session management."""

from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    max_participants: int = Field(default=2, ge=2, le=10)


class SessionCreateResponse(BaseModel):
    session_id: str = Field(max_length=43)
    session_code: str = Field(max_length=12)
    expires_at: datetime


class Participant(BaseModel):
    participant_id: str = Field(max_length=43)
    public_key_armor: str | None = Field(default=None, max_length=65536)
    connected_at: datetime


class SessionState(BaseModel):
    session_id: str = Field(max_length=43)
    created_at: datetime
    last_activity: datetime
    max_participants: int = Field(ge=2, le=10)
    participants: dict[str, Participant] = Field(default_factory=dict)
    # NOTE: no message history — messages are never stored
