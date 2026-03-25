"""Pydantic models for WebSocket message frames."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    KEY_EXCHANGE = "key_exchange"
    CHAT = "chat"
    ACK = "ack"
    TYPING = "typing"
    PRESENCE = "presence"


class KeyExchangeMessage(BaseModel):
    type: Literal[MessageType.KEY_EXCHANGE]
    public_key_armor: str = Field(max_length=65536)


class ChatMessage(BaseModel):
    type: Literal[MessageType.CHAT]
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", max_length=43)
    sender_fingerprint: str = Field(max_length=160)
    ciphertext: str = Field(max_length=65536)
    timestamp: datetime


class AckMessage(BaseModel):
    type: Literal[MessageType.ACK]
    message_id: str = Field(max_length=64)


class TypingIndicator(BaseModel):
    type: Literal[MessageType.TYPING]
    is_typing: bool


class PresenceMessage(BaseModel):
    type: Literal[MessageType.PRESENCE]
    status: Literal["online", "offline"]


# Discriminated union for incoming WS frames — server only accepts these three
IncomingFrame = Annotated[
    KeyExchangeMessage | ChatMessage | TypingIndicator,
    Field(discriminator="type"),
]
