# Design — Phantom Chat

## Overview

Phantom Chat is a zero-persistence, end-to-end encrypted ephemeral messaging application. The guiding principle is that the server must never be able to read a message. All encryption and decryption happens in the browser using OpenPGP.js; the server acts as a ciphertext relay only.

The system is composed of three layers:

- **Client Layer** — Vanilla HTML/CSS/JS with OpenPGP.js 5.x. Generates ephemeral PGP keypairs in-browser, encrypts outbound messages, decrypts inbound messages, and manages the WebSocket connection.
- **Transport Layer** — Caddy v2 reverse proxy on Oracle Cloud. Handles TLS 1.3 termination, automatic certificate management via ACME/Let's Encrypt, HTTP→HTTPS redirect, and security header injection.
- **Application Layer** — FastAPI backend (Python 3.12+). Manages WebSocket sessions, ephemeral key exchange relay, payload validation, rate limiting, and CSRF protection. Holds all state in application memory; nothing is written to disk.

### Key Design Decisions

- **No database** — any external state store is a potential persistence leak. In-memory state is destroyed when the process dies or the session expires.
- **FastAPI native WebSockets over socket.io** — simpler, no additional JS dependency, smaller attack surface.
- **Vanilla JS over React** — minimal JavaScript surface area reduces supply-chain attack risk. OpenPGP.js is the only third-party JS dependency.
- **Caddy over nginx** — automatic HTTPS and zero-config ACME eliminates manual certificate management mistakes; secure defaults out of the box.
- **`secrets.token_urlsafe(32)` for session tokens** — 256-bit URL-safe tokens provide higher entropy than UUID4 alone.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (Client A)                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  OpenPGP.js                                              │   │
│  │  • Generate ephemeral keypair (Curve25519 / RSA-4096)    │   │
│  │  • Encrypt outbound messages with recipient public key   │   │
│  │  • Decrypt inbound ciphertext with local private key     │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  app.js — WebSocket client + UI logic                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ WSS / HTTPS (TLS 1.3)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Caddy v2 (Oracle Cloud VM)                    │
│  • TLS 1.3 termination (ACME/Let's Encrypt)                     │
│  • HTTP → HTTPS redirect                                        │
│  • Security headers (HSTS, CSP, X-Frame-Options, …)            │
│  • IP-stripped access logs                                      │
│  • reverse_proxy localhost:8000                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP/WS (loopback only)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FastAPI Application (uvicorn)                   │
│                                                                  │
│  Middleware stack (outermost → innermost):                       │
│  CORS → CSRF → SecurityHeaders                                  │
│                                                                  │
│  Routes:                                                         │
│  POST   /session          → session.py                          │
│  DELETE /session/{id}     → session.py                          │
│  GET    /health           → health.py                           │
│  WS     /ws/{session_id}  → ws.py                               │
│                                                                  │
│  Services:                                                       │
│  SessionStore   — asyncio.Lock-protected in-memory dict         │
│  PGPValidator   — structural key validation only                │
│  RateLimiter    — sliding-window per-IP in-memory               │
└─────────────────────────────────────────────────────────────────┘
```

### Communication Flow

1. Client A opens the app → browser generates an ephemeral PGP keypair in memory
2. Client A calls `POST /session` → receives a session token (256-bit URL-safe)
3. Client A connects via `WSS /ws/{session_id}` and sends a `KeyExchangeMessage` with its armoured public key
4. Client B joins the session and receives Client A's public key via relay
5. Both clients now hold each other's public keys; private keys never leave the browser
6. Client A encrypts a message with Client B's public key → sends `ChatMessage` with ciphertext
7. Server validates schema and payload size → relays ciphertext to Client B unchanged
8. Client B decrypts locally with its private key
9. On disconnect → server synchronously purges all session state for that participant

---

## Components and Interfaces

### REST API

#### `POST /session`
- **Rate limited**: 10 req/min per IP
- **Request**: `SessionCreateRequest` (optional `max_participants: int`)
- **Response**: `SessionCreateResponse` (session_id, session_code, expires_at)
- **Errors**: 429 Too Many Requests, 422 Validation Error

#### `DELETE /session/{session_id}`
- **Request**: path param `session_id` matching `^[A-Za-z0-9_-]{43}$`
- **Response**: 204 No Content
- **Errors**: 404 Not Found

#### `GET /health`
- **Response**: `{"status": "ok"}` with HTTP 200
- No rate limiting; no auth required

#### `WS /ws/{session_id}`
- **Rate limited**: 10 connections/min per IP
- **Origin check**: must match `settings.allowed_origin`
- **Query param**: `token` — session token for authentication
- **Frame types**: `KeyExchangeMessage`, `ChatMessage`, `AckMessage`, `TypingIndicator`, `PresenceMessage`
- **Max frame size**: 64 KB (close code 1009 if exceeded)

### Services

#### `SessionStore`
```python
class SessionStore:
    async def create_session(self, max_participants: int) -> SessionState: ...
    async def get_session(self, session_id: str) -> SessionState | None: ...
    async def add_participant(self, session_id: str, participant: Participant) -> None: ...
    async def remove_participant(self, session_id: str, participant_id: str) -> None: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def is_empty(self, session_id: str) -> bool: ...
    async def sweep_expired(self) -> None: ...  # background task, runs every 60s
```

#### `PGPValidator`
```python
def validate_public_key_armor(armored_key: str) -> bool:
    # Structural check only — no import, no crypto operations
    ...
```

#### `RateLimiter`
```python
class RateLimiter:
    async def check(self, ip: str, limit: int, window_seconds: int) -> bool: ...
    # Returns True if request is allowed, False if rate limit exceeded
    # Sliding window algorithm, asyncio.Lock-protected in-memory dict
```

### Middleware

#### `SecurityHeadersMiddleware`
Adds to every response:
- `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy: default-src 'self'; script-src 'self'`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy: geolocation=(), camera=(), microphone=()`
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Embedder-Policy: require-corp`

#### `CSRFMiddleware`
- Validates `Origin` header on all state-mutating requests
- Rejects requests with non-JSON `Content-Type` on REST endpoints

### Frontend (`app.js`)

Key responsibilities:
- On load: generate ephemeral PGP keypair via `openpgp.generateKey()`
- Session create/join: REST calls to `/session`
- WebSocket lifecycle: connect, send `KeyExchangeMessage`, handle incoming frames
- Encrypt outbound: `openpgp.encrypt({ message, encryptionKeys: [recipientPublicKey] })`
- Decrypt inbound: `openpgp.decrypt({ message: ciphertext, decryptionKeys: [privateKey] })`
- Display key fingerprint for manual verification
- On tab close / session end: discard all key material (no persistence)

---

## Data Models

### Pydantic Models (Server)

```python
# app/models/session.py

class SessionCreateRequest(BaseModel):
    max_participants: int = Field(default=2, ge=2, le=10)

class SessionCreateResponse(BaseModel):
    session_id: str
    session_code: str  # short human-readable code derived from session_id
    expires_at: datetime

class SessionState(BaseModel):
    session_id: str
    created_at: datetime
    last_activity: datetime
    max_participants: int
    participants: dict[str, Participant]  # participant_id → Participant
    # NOTE: no message history — messages are never stored

class Participant(BaseModel):
    participant_id: str
    public_key_armor: str | None = None  # set after KeyExchangeMessage
    connected_at: datetime
```

```python
# app/models/message.py

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
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    sender_fingerprint: str = Field(max_length=160)
    ciphertext: str = Field(max_length=65536)
    timestamp: datetime

class AckMessage(BaseModel):
    type: Literal[MessageType.ACK]
    message_id: str

class TypingIndicator(BaseModel):
    type: Literal[MessageType.TYPING]
    is_typing: bool

class PresenceMessage(BaseModel):
    type: Literal[MessageType.PRESENCE]
    status: Literal["online", "offline"]

# Discriminated union for incoming WS frames
IncomingFrame = Annotated[
    KeyExchangeMessage | ChatMessage | TypingIndicator,
    Field(discriminator="type")
]
```

### Settings

```python
# app/config.py

class Settings(BaseSettings):
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    session_ttl_minutes: int = 30
    max_session_participants: int = 2
    max_payload_bytes: int = 65536
    rate_limit_per_minute: int = 10
    log_level: str = "WARNING"
    allowed_origin: str  # required, no default

    model_config = SettingsConfigDict(env_file=".env")
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Session token uniqueness

*For any* two calls to `create_session()`, the returned session tokens SHALL be distinct (no collisions across any pair of generated tokens).

**Validates: Requirements 1.1**

### Property 2: Session create-delete round trip

*For any* session, after `create_session()` is called `get_session()` SHALL return the session, and after `delete_session()` is called `get_session()` SHALL return `None`.

**Validates: Requirements 1.2, 1.7**

### Property 3: Session full rejection

*For any* session configured with `max_participants = N`, attempting to add an (N+1)th participant SHALL be rejected with HTTP 403.

**Validates: Requirements 1.4**

### Property 4: Session purged when empty

*For any* session, after all participants have been removed via `remove_participant()`, `get_session()` SHALL return `None` — the session is deleted automatically.

**Validates: Requirements 1.5, 2.5, 2.6**

### Property 5: Session expiry purge

*For any* session whose `last_activity` timestamp is older than `session_ttl_minutes`, after the TTL sweep executes, `get_session()` SHALL return `None`.

**Validates: Requirements 1.6**

### Property 6: Ciphertext relay is opaque

*For any* valid `ChatMessage`, the ciphertext value received by all other participants SHALL be byte-for-byte identical to the ciphertext value sent by the sender — the server MUST NOT transform, truncate, or augment it.

**Validates: Requirements 2.3**

### Property 7: Oversized payload rejection

*For any* WebSocket frame whose encoded byte length exceeds `max_payload_bytes` (65536 bytes), the server SHALL close the connection with WebSocket close code 1009 and the session participant list SHALL remain unchanged.

**Validates: Requirements 2.4**

### Property 8: Message schema validation rejects invalid frames

*For any* WebSocket frame that does not conform to the `IncomingFrame` discriminated union schema (invalid JSON, unknown type, missing required fields), the server SHALL reject it and close the connection without relaying any data to other participants.

**Validates: Requirements 2.2**

### Property 9: Invalid public key rejection

*For any* `KeyExchangeMessage` whose `public_key_armor` field does not pass `validate_public_key_armor()` (including random strings, empty strings, and private key blocks), the server SHALL close the connection with code 1008 and SHALL NOT relay the payload to other participants.

**Validates: Requirements 3.2, 3.4, 3.7**

### Property 10: Rate limit enforcement with Retry-After

*For any* IP address that sends more than `rate_limit_per_minute` requests within the sliding window (to either `POST /session` or `WS /ws/...`), every request beyond the limit SHALL receive HTTP 429 and the response SHALL include a `Retry-After` header.

**Validates: Requirements 7.1, 7.2, 7.4**

### Property 11: Security headers on all responses

*For any* HTTP response returned by the application (any endpoint, any status code), all eight required security headers SHALL be present with their exact specified values.

**Validates: Requirements 6.1**

### Property 12: Origin validation rejects mismatched origins

*For any* HTTP request or WebSocket connection attempt whose `Origin` header does not match `settings.allowed_origin`, the server SHALL reject it — REST requests with HTTP 403 and WebSocket connections with close code 1008 — regardless of the endpoint or payload.

**Validates: Requirements 6.2, 6.4**

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| WebSocket frame > 64 KB | Close with code 1009; log session ID only |
| Invalid JSON frame | Close with code 1007 (invalid frame payload data) |
| Schema validation failure | Close with code 1008; log event type only |
| Invalid/malformed public key | Close with code 1008 |
| Private key detected in frame | Close with code 1008 immediately |
| Session not found | HTTP 404 or WS close 1008 |
| Session full (max participants) | HTTP 403 |
| Rate limit exceeded | HTTP 429 with `Retry-After` |
| Origin mismatch (WS) | WS close 1008 before `accept()` |
| Origin mismatch (REST) | HTTP 403 |
| Non-JSON Content-Type (REST) | HTTP 415 |
| Unexpected server error | HTTP 500; session state unaffected; no content leaked |

**Fail-secure principle**: any unhandled exception in the WebSocket handler MUST trigger participant removal via `try/finally`. The session is never left in a partially-connected state.

```python
try:
    await handle_ws_session(websocket, session_id, participant_id)
finally:
    await session_store.remove_participant(session_id, participant_id)
    if await session_store.is_empty(session_id):
        await session_store.delete_session(session_id)
```

---

## Testing Strategy

### Dual Testing Approach

Both unit tests and property-based tests are required. They are complementary:
- **Unit tests** verify specific examples, integration points, and error conditions
- **Property-based tests** verify universal properties across randomly generated inputs

### Property-Based Testing

Library: **`hypothesis`** (Python)

Each property-based test MUST:
- Run a minimum of 100 iterations (`settings.max_examples = 100`)
- Be tagged with a comment referencing the design property:
  `# Feature: phantom-chat, Property N: <property_text>`
- Test one correctness property per test function

Example:
```python
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

# Feature: phantom-chat, Property 6: ciphertext relay is opaque
@given(ciphertext=st.text(min_size=1, max_size=1000))
@h_settings(max_examples=100)
async def test_ciphertext_relay_is_opaque(ciphertext: str) -> None:
    # arrange: two connected clients
    # act: client A sends ciphertext
    # assert: client B receives byte-for-byte identical ciphertext
    ...
```

### Unit Testing

Framework: **`pytest`** + **`pytest-asyncio`** + **`httpx`**

Focus areas:
- Specific error conditions (wrong origin, oversized payload, malformed key)
- Integration between middleware and route handlers
- Session lifecycle (create → join → disconnect → purge)
- Health endpoint response time

### Test File Mapping

| Test file | Covers |
|-----------|--------|
| `tests/test_session.py` | Session create, delete, expiry, participant limits |
| `tests/test_ws.py` | WS connect, key exchange relay, message relay, disconnect cleanup |
| `tests/test_security.py` | Payload limits, rate limits, security headers, origin checks, CSRF |

### Coverage Gate

- Minimum 80% line coverage enforced in CI via `pytest --cov=app --cov-fail-under=80`

### What Tests MUST NOT Do

- Make real network calls
- Perform real PGP operations (mock `pgp_validator`)
- Write to disk
- Share state between test cases (use isolated `SessionStore` per test via fixtures)
