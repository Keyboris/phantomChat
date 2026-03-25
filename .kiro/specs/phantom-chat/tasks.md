# Tasks — Phantom Chat

## Task List

- [x] 1. Project scaffold and configuration
  - [x] 1.1 Create directory structure (`app/`, `app/routes/`, `app/models/`, `app/services/`, `app/middleware/`, `frontend/`, `infra/`, `tests/`)
  - [x] 1.2 Create `pyproject.toml` with project metadata and tool config (ruff, black, mypy strict, pytest, coverage gate 80%)
  - [x] 1.3 Create `requirements.txt` and `requirements-dev.txt` with all dependencies pinned to exact versions
  - [x] 1.4 Create `.env.example` documenting all environment variables with sensible defaults
  - [x] 1.5 Create `app/config.py` — `Settings` class using `pydantic-settings` with all env vars (`app_host`, `app_port`, `session_ttl_minutes`, `max_session_participants`, `max_payload_bytes`, `rate_limit_per_minute`, `log_level`, `allowed_origin`)
  - [x] 1.6 Create `app/__init__.py` and all package `__init__.py` files with explicit `__all__`
  - [x] 1.7 Create `.pre-commit-config.yaml` with ruff, black, mypy, and detect-secrets hooks
  - [x] 1.8 Create `.github/workflows/ci.yml` — lint → type-check → test → pip-audit pipeline

- [x] 2. Pydantic data models
  - [x] 2.1 Create `app/models/session.py` — `SessionCreateRequest`, `SessionCreateResponse`, `SessionState`, `Participant` models with all field constraints and `max_length` annotations
  - [x] 2.2 Create `app/models/message.py` — `MessageType` enum, `KeyExchangeMessage`, `ChatMessage`, `AckMessage`, `TypingIndicator`, `PresenceMessage`, and `IncomingFrame` discriminated union

- [x] 3. Core services
  - [x] 3.1 Create `app/services/session_store.py` — `SessionStore` class with `asyncio.Lock`-protected `dict[str, SessionState]`; implement `create_session()` using `secrets.token_urlsafe(32)`, `get_session()`, `add_participant()`, `remove_participant()`, `delete_session()`, `is_empty()`, and `sweep_expired()` background task
  - [x] 3.2 Create `app/services/pgp_validator.py` — `validate_public_key_armor(armored_key: str) -> bool` performing structural-only validation (check for valid PGP public key block header/footer, reject private key blocks); MUST NOT import or perform crypto operations
  - [x] 3.3 Create `app/services/rate_limiter.py` — `RateLimiter` class with sliding-window per-IP algorithm using `asyncio.Lock`-protected in-memory dict; `check(ip, limit, window_seconds) -> bool`; return `Retry-After` value

- [x] 4. Middleware
  - [x] 4.1 Create `app/middleware/security_headers.py` — `SecurityHeadersMiddleware` that adds all 8 required headers to every response: HSTS, X-Frame-Options, X-Content-Type-Options, CSP, Referrer-Policy, Permissions-Policy, COOP, COEP
  - [x] 4.2 Create `app/middleware/csrf.py` — `CSRFMiddleware` that validates `Origin` header against `settings.allowed_origin` on all state-mutating requests; rejects non-JSON `Content-Type` on REST endpoints with HTTP 415

- [x] 5. REST route handlers
  - [x] 5.1 Create `app/routes/health.py` — `GET /health` returning `{"status": "ok"}` with HTTP 200; no auth, no rate limiting
  - [x] 5.2 Create `app/routes/session.py` — `POST /session` (rate-limited, creates session via `SessionStore`, returns `SessionCreateResponse`) and `DELETE /session/{session_id}` (validates session_id pattern `^[A-Za-z0-9_-]{43}$`, purges session, returns 204)

- [x] 6. WebSocket handler
  - [x] 6.1 Create `app/routes/ws.py` — `WS /ws/{session_id}` handler that: checks Origin header before `accept()` (close 1008 on mismatch), validates session exists (close 1008 if not), enforces rate limit (429 if exceeded), accepts connection, requires `KeyExchangeMessage` as first frame, validates public key via `pgp_validator`, stores key in session, broadcasts key to other participants, then enters message loop
  - [x] 6.2 Implement message loop in `ws.py` — for each frame: check payload size (close 1009 if > `max_payload_bytes`), validate against `IncomingFrame` schema (close 1007/1008 on failure), reject frames containing `BEGIN PGP PRIVATE KEY` (close 1008), relay `ChatMessage` ciphertext unchanged to all other participants, send `AckMessage` to sender, handle `TypingIndicator` and `PresenceMessage` relay
  - [x] 6.3 Implement disconnect cleanup in `ws.py` — wrap entire session handler in `try/finally`; on any exit (normal or exception) call `session_store.remove_participant()` then `session_store.delete_session()` if session is empty

- [x] 7. FastAPI app factory
  - [x] 7.1 Create `app/main.py` — `create_app()` factory that instantiates `FastAPI`, registers routers, adds middleware in order (CORS → CSRF → SecurityHeaders), mounts `frontend/` as static files, and wires up `lifespan` context manager that starts the TTL sweep background task on startup and cancels it on shutdown

- [x] 8. Frontend
  - [x] 8.1 Create `frontend/index.html` — single-page app with session create/join UI, chat message area, key fingerprint display panel, and connection status indicator
  - [x] 8.2 Create `frontend/app.js` — on load generate ephemeral PGP keypair via `openpgp.generateKey()` (Curve25519); implement session create/join via REST; implement WebSocket lifecycle (connect, send `KeyExchangeMessage`, handle incoming frames); encrypt outbound messages with recipient public key; decrypt inbound ciphertext with local private key; display key fingerprint; discard all key material on tab close (no localStorage/IndexedDB writes)
  - [x] 8.3 Create `frontend/style.css` — minimal styling for the chat UI

- [x] 9. Infrastructure configuration
  - [x] 9.1 Create `infra/Caddyfile` — production config with `admin off`, TLS 1.3 minimum, AEAD cipher suites only, IP-stripped access logs, security headers, `reverse_proxy localhost:8000`
  - [x] 9.2 Create `infra/Caddyfile.dev` — local dev config (HTTP only, no TLS)
  - [x] 9.3 Create `infra/phantom-chat.service` — systemd unit file with hardening directives: `User=phantom`, `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, `ProtectHome=true`, `RestrictNamespaces=true`, `CapabilityBoundingSet=`, `SystemCallFilter=@system-service`
  - [x] 9.4 Create `infra/docker-compose.yml` — local dev environment (FastAPI + Caddy dev config)

- [x] 10. Test suite
  - [x] 10.1 Create `tests/conftest.py` — pytest fixtures: `async_client` (httpx `AsyncClient`), isolated `session_store` instance per test, mock `pgp_validator` that returns `True` by default
  - [x] 10.2 Create `tests/test_session.py` — unit and property tests for session lifecycle:
    - Example: create session returns valid token and response shape
    - Example: join session with valid code succeeds
    - Property (Design Property 1): two `create_session()` calls return distinct tokens
    - Property (Design Property 2): create → get → delete → get returns None
    - Property (Design Property 3): adding participant beyond `max_participants` returns HTTP 403
    - Property (Design Property 4): remove all participants → session deleted
    - Property (Design Property 5): session with expired `last_activity` is purged by sweep
    - Example: `DELETE /session/{id}` returns 204 and session is gone
    - Example: `DELETE /session/{unknown_id}` returns 404
  - [x] 10.3 Create `tests/test_ws.py` — unit and property tests for WebSocket behavior:
    - Example: client connects, sends `KeyExchangeMessage`, key is relayed to other participant
    - Example: client joins mid-session receives no prior messages
    - Example: ACK frame sent to sender after relay
    - Property (Design Property 6): ciphertext received by recipient equals ciphertext sent by sender
    - Property (Design Property 7): frame > 64 KB closes connection with code 1009
    - Property (Design Property 8): invalid schema frame closes connection without relay
    - Property (Design Property 9): frame with `BEGIN PGP PRIVATE KEY` closes connection with code 1008
    - Example (`test_session_purged_on_disconnect`): connect → disconnect → session_store is empty
    - Example (`test_origin_mismatch_rejected`): WS connect with wrong Origin → close code 1008
  - [x] 10.4 Create `tests/test_security.py` — security-focused unit and property tests:
    - Property (Design Property 10): 11th request from same IP within window returns 429 with `Retry-After`
    - Property (Design Property 11): every endpoint response contains all 8 security headers
    - Property (Design Property 12): request/connection with mismatched Origin is rejected
    - Example (`test_server_cannot_read_messages`): relay intercept confirms opaque forwarding
    - Example (`test_private_key_never_transmitted`): no WS frame in E2E flow contains private key marker
    - Example (`test_session_expires_after_ttl`): fast-forward TTL → session deleted
    - Example: non-JSON Content-Type on REST endpoint returns HTTP 415
    - Example: CORS `allow_origins` is never `*`
