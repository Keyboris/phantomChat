# Product Overview — Phantom Chat

## What is Phantom Chat?

Phantom Chat is a **zero-persistence, end-to-end encrypted ephemeral messaging application**. It is designed from first principles to maximise operational security: the server learns as little as possible, nothing is ever stored, and all cryptographic operations happen on the client.

## Core Value Proposition

> "When the conversation ends, it never existed."

- **Zero Knowledge** — the server never processes plaintext. It only relays PGP ciphertext.
- **Zero Persistence** — no database, no logs, no chat history. All state lives in application memory and is destroyed on disconnect.
- **No Identity** — no accounts, no email addresses, no tracking. A session code is all that is needed.
- **Strong Encryption** — PGP end-to-end encryption using OpenPGP.js in-browser. Private keys never leave the client.

## Target Users

Security-conscious individuals who need to communicate sensitive information without any server-side record: journalists, lawyers, activists, or anyone who values ephemeral communication.

## Key User Journeys

### Start a Session
1. User visits the app → browser auto-generates an ephemeral PGP keypair
2. User clicks "Create Session" → receives a shareable session code
3. User shares the code out-of-band with their contact

### Join a Session
1. Contact opens the app, enters the session code
2. Both clients exchange public keys (relayed by server, not stored)
3. Chat begins — all messages encrypted before transmission

### End a Session
1. User closes the tab or clicks "End Session"
2. Server immediately purges all in-memory session state
3. No trace of the conversation remains anywhere

## Success Metrics (MVP)

| Metric | Target |
|--------|--------|
| TLS rating (testssl.sh) | A+ |
| OWASP ZAP high findings | 0 |
| Known CVEs (pip-audit) | 0 |
| Message round-trip latency | < 100ms |
| Concurrent sessions (Always Free VM) | ≥ 50 |

## Non-Goals

- User accounts or persistent identity
- Multi-device sync
- File or media transfer
- Message delivery guarantees beyond best-effort WebSocket
- Mobile native apps (MVP is browser-only)
- Traffic analysis / metadata protection (Tor out of scope for MVP)
