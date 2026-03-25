# Phantom Chat

> "When the conversation ends, it never existed."

Zero-persistence, end-to-end encrypted ephemeral messaging. The server never sees plaintext — it only relays PGP ciphertext. No accounts, no database, no logs.

## How it works

1. Open the app — your browser generates an ephemeral PGP keypair (Curve25519) in memory
2. Create a session and share the code out-of-band with your contact
3. Both clients exchange public keys over WebSocket; private keys never leave the browser
4. All messages are encrypted client-side with OpenPGP.js before transmission
5. Close the tab — all server-side state is immediately purged

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 · FastAPI · uvicorn |
| WebSockets | FastAPI native (Starlette) |
| Validation | Pydantic v2 |
| Encryption (client) | OpenPGP.js 5.x |
| Reverse proxy | Caddy v2 (automatic TLS) |
| Infrastructure | Oracle Cloud Always Free VM · Ubuntu 22.04 |
| Process manager | systemd |

## Quick start (local dev)

```bash
# 1. Clone and create a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set ALLOWED_ORIGIN to your domain or http://localhost:8000 for local dev

# 4. Start the server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 5. Open http://localhost:8000 in two browser tabs
```

### Docker (local dev)

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up
# App at http://localhost:8080
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST` | `127.0.0.1` | Bind address (loopback only in production) |
| `APP_PORT` | `8000` | Port for uvicorn |
| `SESSION_TTL_MINUTES` | `30` | Session inactivity timeout |
| `MAX_SESSION_PARTICIPANTS` | `2` | Max participants per session |
| `MAX_PAYLOAD_BYTES` | `65536` | WebSocket frame size limit (64 KB) |
| `RATE_LIMIT_PER_MINUTE` | `10` | Max requests/min per IP |
| `LOG_LEVEL` | `WARNING` | Never set to DEBUG in production |
| `ALLOWED_ORIGIN` | *(required)* | Exact origin for CORS and CSRF checks |

Never commit `.env`. See `.env.example` for the full template.

## Running tests

```bash
pytest --cov=app --cov-report=term-missing tests/
```

Individual test files:

```bash
pytest tests/test_session.py -v    # session lifecycle + property tests
pytest tests/test_ws.py -v         # WebSocket + PGP validator tests
pytest tests/test_security.py -v   # headers, rate limiting, CSRF, origin
```

Linting and type checks:

```bash
ruff check .
black --check .
mypy --strict app/
pip-audit -r requirements.txt
```

## Production deployment (Oracle Cloud)

### 1. Provision the VM

- Oracle Cloud Always Free — Ubuntu 22.04 LTS, AMD, 1 OCPU, 1 GB RAM
- Open inbound TCP 80 and 443 in the Oracle Security List
- Restrict port 22 to your known IP

### 2. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow from <your-ip> to any port 22
sudo ufw enable
```

### 3. Install Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Copy `infra/Caddyfile` to `/etc/caddy/Caddyfile` and replace `your-domain.example.com` with your actual domain.

### 4. Deploy the app

**What to copy and where**

From your working machine, copy these files and directories to `/opt/phantom-chat/` on the VM. Nothing else is needed at runtime.

```
Your machine                          →  VM: /opt/phantom-chat/
─────────────────────────────────────────────────────────────
app/                                  →  app/
frontend/                             →  frontend/
requirements.txt                      →  requirements.txt
.env.example                          →  .env.example
```

Also copy the Caddyfile and systemd unit to their system locations:

```
infra/Caddyfile                       →  /etc/caddy/Caddyfile
infra/phantom-chat.service            →  /etc/systemd/system/phantom-chat.service
```

**Do NOT copy:** `.env`, `tests/`, `requirements-dev.txt`, `.github/`, `.kiro/`, `infra/docker-compose.yml`, `pyproject.toml`, `*.pdf`

**Transfer command (run from your machine):**

```bash
# Replace user@your-vm-ip with your actual VM user and IP
scp -r app/ frontend/ requirements.txt .env.example \
    user@your-vm-ip:/opt/phantom-chat/

scp infra/Caddyfile \
    user@your-vm-ip:/tmp/Caddyfile

scp infra/phantom-chat.service \
    user@your-vm-ip:/tmp/phantom-chat.service
```

**On the VM, finish the setup:**

```bash
# Create the service user
sudo useradd -r -s /bin/false phantom

# Move system files into place
sudo mv /tmp/Caddyfile /etc/caddy/Caddyfile
sudo mv /tmp/phantom-chat.service /etc/systemd/system/phantom-chat.service

# Edit the Caddyfile — replace your-domain.example.com with your actual domain
sudo nano /etc/caddy/Caddyfile

# Set up the Python environment
sudo python3 -m venv /opt/phantom-chat/.venv
sudo /opt/phantom-chat/.venv/bin/pip install -r /opt/phantom-chat/requirements.txt

# Create and configure the .env file
sudo cp /opt/phantom-chat/.env.example /opt/phantom-chat/.env
sudo nano /opt/phantom-chat/.env
# Set: ALLOWED_ORIGIN=https://your-domain.example.com
# Set: LOG_LEVEL=WARNING

# Lock down ownership
sudo chown -R phantom:phantom /opt/phantom-chat
sudo chmod 600 /opt/phantom-chat/.env
```

### 5. Enable systemd service

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-chat
sudo systemctl enable --now caddy
sudo systemctl status phantom-chat
sudo systemctl status caddy
```

Check logs if anything fails:

```bash
sudo journalctl -u phantom-chat -f
sudo journalctl -u caddy -f
```

### 6. Verify TLS

```bash
# Should return A+
testssl.sh https://your-domain.example.com
```

## Security model

| Principle | Implementation |
|-----------|---------------|
| Zero Knowledge | Server only relays PGP ciphertext — never sees plaintext |
| Zero Persistence | No database, no logs, all state in application memory |
| No Identity | No accounts, no email, no tracking |
| Fail Secure | Any error closes the session; cleanup in `try/finally` |
| Minimal Surface | No ORM, no auth library, no caching layer |

### What the server never does

- Read, log, or store message content (ciphertext or plaintext)
- Generate or handle private keys
- Buffer or replay messages
- Write anything to disk

### Threat model (STRIDE summary)

| Threat | Status |
|--------|--------|
| Spoofing | Mitigated — ephemeral tokens + manual fingerprint verification |
| Tampering | Mitigated — TLS in transit + PGP signature verification on client |
| Repudiation | Accepted — no message logs by design |
| Info Disclosure | Mitigated — server never sees plaintext; ephemeral memory only |
| DoS | Partially mitigated — rate limiting, payload limits, session caps |
| Elevation of Privilege | Mitigated — no auth system, no persistent roles |
| Traffic Analysis | Accepted/Noted — Tor/VPN recommended for metadata hiding (out of scope) |

## Project structure

```
phantom-chat/
├── app/
│   ├── main.py               # App factory, ConnectionManager, lifespan
│   ├── config.py             # pydantic-settings (all config via env vars)
│   ├── routes/
│   │   ├── health.py         # GET /health
│   │   ├── session.py        # POST /session, DELETE /session/{id}
│   │   └── ws.py             # WS /ws/{session_id}
│   ├── models/
│   │   ├── session.py        # SessionState, Participant, request/response models
│   │   └── message.py        # ChatMessage, KeyExchangeMessage, IncomingFrame union
│   ├── services/
│   │   ├── session_store.py  # asyncio.Lock-protected in-memory registry + TTL sweep
│   │   ├── pgp_validator.py  # Structural-only public key validation
│   │   └── rate_limiter.py   # Sliding-window per-IP rate limiter
│   └── middleware/
│       ├── security_headers.py  # 8 required security headers on every response
│       └── csrf.py              # Origin validation + Content-Type enforcement
├── frontend/
│   ├── index.html            # Single-page app
│   ├── app.js                # OpenPGP.js key gen, WS client, encrypt/decrypt
│   └── style.css
├── infra/
│   ├── Caddyfile             # Production (TLS 1.3, IP-stripped logs)
│   ├── Caddyfile.dev         # Local dev (HTTP only)
│   ├── phantom-chat.service  # systemd unit with hardening directives
│   └── docker-compose.yml    # Local dev environment
└── tests/
    ├── conftest.py           # Fixtures: async_client, session_store, mocked pgp_validator
    ├── test_session.py       # Session lifecycle + property-based tests
    ├── test_ws.py            # WebSocket + PGP validator tests
    └── test_security.py      # Headers, rate limiting, CSRF, origin checks
```

## Acceptance criteria

| Test | Assertion |
|------|-----------|
| SAT-01 | WS stream interception yields only PGP ciphertext |
| SAT-02 | No message content recoverable after disconnect |
| SAT-03 | TLS audit via testssl.sh — A+ rating |
| SAT-04 | OWASP ZAP passive scan — zero high-severity findings |
| SAT-05 | pip-audit — zero known CVEs in production dependencies |
| FAT-01 | Two clients exchange PGP-encrypted messages end-to-end |
| FAT-02 | Session expires and purges correctly after TTL |
| FAT-03 | Oversized payload (>64 KB) rejected with close code 1009 |
| FAT-04 | Rate limit triggers 429 after 10 requests/min |
| FAT-05 | Health endpoint returns 200 OK within 500ms |
