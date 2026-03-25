# Project Structure — Phantom Chat

## Directory Layout

```
phantom-chat/
├── .kiro/
│   └── steering/
│       ├── product.md        # What we're building and why
│       ├── tech.md           # Tech stack and tooling decisions
│       ├── structure.md      # This file — codebase layout
│       └── security.md       # Security rules and constraints
│
├── app/                      # FastAPI application package
│   ├── __init__.py
│   ├── main.py               # App factory — creates FastAPI instance, registers routers/middleware
│   ├── config.py             # pydantic-settings Settings class (all env vars here)
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── session.py        # POST /session, DELETE /session/{id}
│   │   ├── health.py         # GET /health
│   │   └── ws.py             # WebSocket /ws/{session_id}
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── session.py        # SessionCreate, SessionResponse, SessionState (Pydantic)
│   │   └── message.py        # WSMessage, KeyExchangeMessage, ChatMessage (Pydantic)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── session_store.py  # Thread-safe in-memory session registry + TTL management
│   │   ├── pgp_validator.py  # Structural-only validation of armoured public keys
│   │   └── rate_limiter.py   # Sliding-window per-IP rate limiter (in-memory)
│   │
│   └── middleware/
│       ├── __init__.py
│       ├── security_headers.py  # HSTS, CSP, X-Frame-Options etc.
│       └── csrf.py              # Origin / SameSite validation
│
├── frontend/
│   ├── index.html            # Single-page app (served as static file)
│   ├── app.js                # OpenPGP.js integration, WS client, UI logic
│   └── style.css
│
├── infra/
│   ├── Caddyfile             # Production Caddy config
│   ├── Caddyfile.dev         # Local dev Caddy config (HTTP only)
│   ├── phantom-chat.service  # systemd unit file
│   └── docker-compose.yml    # Local dev environment
│
├── tests/
│   ├── conftest.py           # pytest fixtures (TestClient, mock session store)
│   ├── test_session.py       # Session create/delete/expiry
│   ├── test_ws.py            # WebSocket connect, key exchange, message relay
│   └── test_security.py      # Payload limits, rate limits, header checks
│
├── .github/
│   └── workflows/
│       └── ci.yml            # Lint → type-check → test → audit
│
├── .pre-commit-config.yaml
├── .env.example              # Documented env var template (never commit .env)
├── .gitignore
├── pyproject.toml            # Project metadata + tool config (ruff, black, mypy, pytest)
├── requirements.txt          # Pinned production deps
├── requirements-dev.txt      # Pinned dev/test deps
└── README.md
```

## Module Responsibilities

### `app/main.py`
- Creates the `FastAPI` instance
- Registers all routers (`session`, `health`, `ws`)
- Adds middleware in correct order: CORS (restrictive) → CSRF → SecurityHeaders
- Mounts `frontend/` as a static files directory
- Starts/stops background TTL cleanup task (via `lifespan` context manager)

### `app/config.py`
- Single `Settings` class using `pydantic-settings`
- All configuration via environment variables
- Provides a cached `get_settings()` dependency for injection

### `app/services/session_store.py`
- `SessionStore` class: `asyncio.Lock`-protected `dict[str, SessionState]`
- Methods: `create_session()`, `get_session()`, `add_participant()`, `remove_participant()`, `delete_session()`
- Background task: sweep expired sessions every 60 seconds
- **MUST NOT** store any message content — only participant metadata and public keys

### `app/routes/ws.py`
- Accepts WSS connection, validates session token from query param
- Receives `KeyExchangeMessage` → stores public key → broadcasts to room
- Receives `ChatMessage` → validates schema + size → broadcasts ciphertext to room
- On disconnect: calls `session_store.remove_participant()` immediately

### `app/services/pgp_validator.py`
- Single function: `validate_public_key_armor(armored_key: str) -> bool`
- Checks that the string is a valid armoured PGP public key block
- **MUST NOT** import/decrypt the key or perform any operation beyond structural validation

## Coding Conventions

- **One file per router** — no mega-route files
- **Dependency injection via FastAPI `Depends()`** — never import `settings` directly in routes
- **All async** — use `async def` for all route handlers and WebSocket endpoints
- **Pydantic models for everything crossing a boundary** — no raw dicts in route signatures
- **No global mutable state** except the `SessionStore` singleton (injected via `Depends()`)
- **Explicit `__all__`** in every `__init__.py`
- **Type annotations on every function** — mypy strict compliance enforced in CI

## Test Conventions

- Use `pytest-asyncio` for all async tests
- `conftest.py` provides: `async_client` (httpx), `session_store` (isolated instance per test)
- Tests MUST NOT make real network calls or real PGP operations — mock `pgp_validator`
- Each test file maps 1:1 to a module in `app/`
- Test names: `test_<what>_<condition>_<expected_result>`

## What Kiro Should Never Generate

- Any code that logs, stores, or returns message content (ciphertext or plaintext)
- Any database models, ORM code, or file I/O for messages
- Any user authentication or account management code
- Any code that transmits private keys or generates keypairs server-side
- Global mutable state outside of `SessionStore`
- Synchronous route handlers (`def` instead of `async def`)
- Direct `os.environ` access outside of `config.py`
