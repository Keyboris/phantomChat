# Security Rules — Phantom Chat

> **These rules are ABSOLUTE constraints. Kiro must follow them in every code generation task, without exception.**

## The Prime Directive

**The server must never be able to read a message.**

Every architectural decision, every function, every line of code must be evaluated against this principle. When in doubt, choose the option that gives the server less information.

---

## Absolute Prohibitions (Never Generate This Code)

### 1. No plaintext message handling
```python
# ❌ NEVER — server reading message content
def process_message(content: str) -> str:
    return content.upper()

# ✅ CORRECT — server treats content as opaque bytes
async def relay_message(frame: ChatMessage) -> None:
    await broadcast_to_room(frame.session_id, frame.ciphertext)
```

### 2. No logging of message data
```python
# ❌ NEVER — logging any message field
logger.info(f"Message from {sender}: {message.ciphertext}")
logger.debug(f"Payload: {frame}")

# ✅ CORRECT — log only session-level events, never content
logger.info(f"Message relayed in session {session_id[:8]}...")
```

### 3. No persistence of any kind
```python
# ❌ NEVER — any file, DB, or cache write for messages
with open("chat.log", "a") as f:
    f.write(message)
redis.set(f"msg:{id}", ciphertext)
session["history"].append(message)

# ✅ CORRECT — fire and forget
await websocket.send_text(ciphertext)
```

### 4. No server-side key generation or private key handling
```python
# ❌ NEVER
keypair = pgp.generate_keypair()
private_key = decrypt_session_key(encrypted_key)

# ✅ CORRECT — structural validation only
def validate_public_key_armor(key: str) -> bool:
    return key.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
```

### 5. No hardcoded secrets
```python
# ❌ NEVER
SECRET_KEY = "my-super-secret"
DB_URL = "postgresql://user:pass@localhost/db"

# ✅ CORRECT
class Settings(BaseSettings):
    secret_key: str = Field(..., min_length=32)
```

---

## Required Security Patterns

### Payload Size Enforcement
Every WebSocket frame handler MUST reject oversized payloads before any processing:

```python
MAX_BYTES = settings.max_payload_bytes  # default 65536

async def websocket_handler(websocket: WebSocket, ...):
    data = await websocket.receive_text()
    if len(data.encode()) > MAX_BYTES:
        await websocket.close(code=1009, reason="Payload too large")
        return
```

### Rate Limiting
Session creation (`POST /session`) and WebSocket connection (`GET /ws/...`) MUST be rate-limited:

```python
# Sliding window: max N requests per minute per IP
# Return HTTP 429 with Retry-After header when exceeded
# Use asyncio-based in-memory limiter — no Redis required
```

### Session Token Generation
Tokens MUST be generated with `secrets.token_urlsafe(32)` — never `uuid4()` alone (lower entropy):

```python
import secrets
token = secrets.token_urlsafe(32)  # 256-bit URL-safe token
```

### Security Headers (all responses)
The `SecurityHeadersMiddleware` MUST add these headers to every response:

```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Content-Security-Policy: default-src 'self'; script-src 'self'
Referrer-Policy: no-referrer
Permissions-Policy: geolocation=(), camera=(), microphone=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

### CORS Policy
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.allowed_origin],  # explicit domain, never "*"
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
```

### WebSocket Origin Check
```python
async def websocket_endpoint(websocket: WebSocket):
    origin = websocket.headers.get("origin", "")
    if origin != settings.allowed_origin:
        await websocket.close(code=1008)
        return
    await websocket.accept()
```

### Session Cleanup on Disconnect
```python
# MUST be in a try/finally block — cleanup is non-negotiable
try:
    await handle_ws_session(websocket, session_id)
finally:
    await session_store.remove_participant(session_id, participant_id)
    # If room is now empty, delete the entire session
    if await session_store.is_empty(session_id):
        await session_store.delete_session(session_id)
```

---

## Input Validation Rules

- All REST request bodies MUST be validated by a Pydantic model before any processing
- `session_id` path parameters MUST match pattern `^[A-Za-z0-9_-]{43}$` (URL-safe base64, 32-byte token)
- PGP public key input MUST be validated by `pgp_validator.validate_public_key_armor()` before relay
- Reject any payload containing null bytes (`\x00`) — reject with HTTP 400
- All string fields MUST have `max_length` constraints in Pydantic models

---

## What the Test Suite MUST Verify

| Test | Assertion |
|------|-----------|
| `test_server_cannot_read_messages` | Intercept WS relay; confirm value forwarded equals value sent (opaque relay, no transformation) |
| `test_session_purged_on_disconnect` | Connect → disconnect → inspect session_store → assert empty |
| `test_oversized_payload_rejected` | Send 65537-byte frame → assert WS close code 1009 |
| `test_rate_limit_enforced` | 11 requests in 60s → assert 429 on 11th |
| `test_security_headers_present` | GET any endpoint → assert all 8 security headers present |
| `test_private_key_never_transmitted` | Full E2E flow → assert no WS frame contains `BEGIN PGP PRIVATE KEY` |
| `test_session_expires_after_ttl` | Create session → fast-forward TTL → assert session deleted |
| `test_origin_mismatch_rejected` | WS connect with wrong Origin → assert close code 1008 |

---

## Dependency Hygiene

- Run `pip-audit` in CI; **fail the build** on any known CVE (no exceptions)
- Run `detect-secrets scan --baseline .secrets.baseline` in pre-commit
- Minimise dependencies — evaluate every new package against security cost
- Allowed production dependencies (add to this list deliberately):
  - `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `python-gnupg`
  - No ORM, no database driver, no caching library, no auth library

---

## Oracle Cloud / Caddy Hardening Checklist

When generating `Caddyfile` or infrastructure config:

- [ ] `admin off` — disable Caddy admin API
- [ ] `protocols h1 h2` only — no h3/QUIC until further review
- [ ] `min_version tls1.3` — TLS 1.2 disabled
- [ ] IP addresses stripped from access logs
- [ ] `reverse_proxy localhost:8000` — bind FastAPI to loopback only
- [ ] Oracle Security List: inbound TCP 80, 443 only (22 restricted to known IP)
- [ ] UFW: `ufw default deny incoming` before opening 80/443

## systemd Hardening (phantom-chat.service)

```ini
[Service]
User=phantom
Group=phantom
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
RestrictNamespaces=true
RestrictRealtime=true
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallFilter=@system-service
```
