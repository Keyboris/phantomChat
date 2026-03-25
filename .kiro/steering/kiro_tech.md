# Technical Stack — Phantom Chat

## Runtime & Framework

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | Python 3.12+ | Strict type annotations required (mypy strict) |
| Web framework | FastAPI 0.111+ | ASGI via uvicorn |
| WebSockets | FastAPI native (Starlette) | WSS only — no socket.io |
| Data validation | Pydantic v2 | All request/response models MUST use Pydantic |
| Settings | pydantic-settings | Environment-based config, no hardcoded values |
| PGP (server) | python-gnupg | Structural key validation only — never decrypt |
| PGP (client) | OpenPGP.js 5.x | All encryption/decryption in-browser |
| Frontend | Vanilla HTML/CSS/JS | No React/Vue — minimal footprint, no npm bundler |

## Infrastructure

| Component | Technology | Notes |
|-----------|-----------|-------|
| Reverse proxy | Caddy v2 | Automatic TLS via ACME/Let's Encrypt |
| Cloud VM | Oracle Cloud Always Free | Ubuntu 22.04 LTS, AMD, 1 OCPU min |
| Process manager | systemd | Auto-restart on failure |
| Firewall | UFW + Oracle Security List | Only ports 80, 443, 22 open |

## Development Tooling

| Tool | Purpose |
|------|---------|
| ruff | Linting (replaces flake8/isort) |
| black | Code formatting |
| mypy --strict | Static type checking |
| pytest + pytest-asyncio | Test framework |
| httpx | Async HTTP test client |
| detect-secrets | Pre-commit secret scanning |
| pip-audit | Dependency CVE scanning |
| pre-commit | Git hook runner |

## Dependency Management

- **All production dependencies MUST be pinned to exact versions** in `requirements.txt`
- **Dev dependencies** pinned in `requirements-dev.txt`
- Run `pip-audit` in CI — fail build on any known CVE
- `pyproject.toml` is the single source of truth for project metadata and tool config

## Key Architectural Decisions

### Why FastAPI WebSockets over socket.io?
FastAPI native WebSockets are simpler, have no additional JS dependency on the client, and reduce attack surface.

### Why no Redis / external store?
Any external state store is a potential persistence leak. All session state in application memory ensures it is destroyed when the process dies or the session expires.

### Why Vanilla JS over React?
Minimal JavaScript surface area reduces the risk of supply-chain attacks. OpenPGP.js is the only third-party JS dependency.

### Why Caddy over nginx?
Caddy's automatic HTTPS and zero-config ACME support eliminates manual certificate management mistakes. Its defaults are also more secure out of the box.

## Environment Variables (via pydantic-settings)

```
APP_HOST=127.0.0.1          # Bind only to localhost (Caddy fronts it)
APP_PORT=8000
SESSION_TTL_MINUTES=30
MAX_SESSION_PARTICIPANTS=2
MAX_PAYLOAD_BYTES=65536     # 64 KB hard limit on WS frames
RATE_LIMIT_PER_MINUTE=10    # Session create + WS connect
LOG_LEVEL=WARNING           # Never DEBUG in production
```

All variables MUST have sensible defaults and be documented in `.env.example`. **Never commit `.env`.**

## Caddy Configuration Principles

```caddyfile
{
    # Disable admin API in production
    admin off
    # Strict TLS
    servers {
        protocols h1 h2
        tls_connection_policies {
            min_version tls1.3
        }
    }
}

chat.example.com {
    # Strip IP from logs
    log { format filter { ... } }

    # Security headers
    header {
        Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
        X-Frame-Options DENY
        X-Content-Type-Options nosniff
        Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'"
        Referrer-Policy no-referrer
        -Server
    }

    reverse_proxy localhost:8000
}
```

## CI/CD Pipeline (GitHub Actions)

1. `ruff check .` — linting
2. `black --check .` — formatting
3. `mypy --strict app/` — type checking
4. `pytest --cov=app tests/` — tests (coverage gate: 80%)
5. `pip-audit` — CVE check (fail on any)
6. `detect-secrets scan` — no secrets in code
