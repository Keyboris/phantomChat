/**
 * Phantom Chat — client-side logic
 *
 * Security invariants:
 * - Private key NEVER leaves this module (no postMessage, no storage, no network)
 * - All encryption/decryption happens here, in-browser
 * - No localStorage, sessionStorage, or IndexedDB writes
 * - Key material is discarded when the tab closes (lives only in module scope)
 */

import * as openpgp from '/openpgp.min.mjs';

// ── State (module scope only — never persisted) ──────────────────────────────
let privateKey = null;
let publicKeyArmor = '';
let myFingerprint = '';
let peerPublicKey = null;
let peerFingerprint = '';
let ws = null;
let sessionId = '';

// ── DOM refs ─────────────────────────────────────────────────────────────────
const screenSetup   = document.getElementById('screen-setup');
const screenChat    = document.getElementById('screen-chat');
const keyStatus     = document.getElementById('key-status');
const btnCreate     = document.getElementById('btn-create');
const btnJoin       = document.getElementById('btn-join');
const inputCode     = document.getElementById('input-code');
const statusDot     = document.getElementById('status-indicator');
const sessionDisplay = document.getElementById('session-code-display');
const myFpEl        = document.getElementById('my-fingerprint');
const peerFpEl      = document.getElementById('peer-fingerprint');
const messagesEl    = document.getElementById('messages');
const typingEl      = document.getElementById('typing-indicator');
const formSend      = document.getElementById('form-send');
const inputMsg      = document.getElementById('input-message');
const btnSend       = document.getElementById('btn-send');
const btnEnd        = document.getElementById('btn-end');

// ── Key generation ────────────────────────────────────────────────────────────
async function generateKeypair() {
  // format: 'armored' returns strings immediately — no async serialisation needed
  const { privateKey: privArmored, publicKey: pubArmored } = await openpgp.generateKey({
    type: 'ecc',
    curve: 'curve25519',
    userIDs: [{ name: 'phantom' }],
    format: 'armored',
  });
  // Parse back to key objects for crypto operations
  privateKey = await openpgp.readPrivateKey({ armoredKey: privArmored });
  publicKeyArmor = pubArmored;
  // getFingerprint() is synchronous in OpenPGP.js 5.x
  const pub = await openpgp.readKey({ armoredKey: pubArmored });
  const fp = pub.getFingerprint();
  myFingerprint = fp.toUpperCase().match(/.{1,4}/g).join(' ');
}

// ── Startup ───────────────────────────────────────────────────────────────────
(async () => {
  try {
    await generateKeypair();
    keyStatus.textContent = 'Keypair ready';
    keyStatus.className = 'status ready';
    btnCreate.disabled = false;
    btnJoin.disabled = false;
  } catch (err) {
    keyStatus.textContent = 'Key generation failed — reload to retry';
    console.error('Key generation error:', err);
  }
})();

// ── Session create ────────────────────────────────────────────────────────────
btnCreate.addEventListener('click', async () => {
  btnCreate.disabled = true;
  try {
    const res = await fetch('/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_participants: 2 }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    sessionId = data.session_id;
    connectWS(data.session_id);  // pass full session_id — displayed for sharing
  } catch (err) {
    alert('Failed to create session. Try again.');
    btnCreate.disabled = false;
    console.error(err);
  }
});

// ── Session join ──────────────────────────────────────────────────────────────
btnJoin.addEventListener('click', async () => {
  const raw = inputCode.value.trim();
  if (!raw) return;

  // Users share the full session_id (43-char URL-safe token) out-of-band.
  // The short session_code (12 chars) shown in the UI is display-only —
  // the server only accepts the full 43-char session_id on the WS endpoint.
  if (raw.length !== 43) {
    alert('Invalid session code. Please paste the full session ID shared by your contact (43 characters).');
    return;
  }

  sessionId = raw;
  connectWS(raw.slice(0, 12).toUpperCase());
});

// ── WebSocket lifecycle ───────────────────────────────────────────────────────
function connectWS(displayCode) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws/${sessionId}`;

  setStatus('connecting');
  ws = new WebSocket(url);

  ws.addEventListener('open', () => {
    setStatus('connected');
    showChat(displayCode);
    // First frame MUST be key_exchange
    ws.send(JSON.stringify({ type: 'key_exchange', public_key_armor: publicKeyArmor }));
    myFpEl.textContent = myFingerprint;
  });

  ws.addEventListener('message', (event) => handleFrame(event.data));

  ws.addEventListener('close', () => {
    setStatus('disconnected');
    inputMsg.disabled = true;
    btnSend.disabled = true;
    appendSystemMsg('Disconnected.');
  });

  ws.addEventListener('error', () => {
    appendSystemMsg('Connection error.');
  });
}

// ── Incoming frame handler ────────────────────────────────────────────────────
async function handleFrame(raw) {
  let frame;
  try { frame = JSON.parse(raw); } catch { return; }

  switch (frame.type) {
    case 'key_exchange':
      await handleKeyExchange(frame.public_key_armor);
      break;
    case 'chat':
      await handleChat(frame);
      break;
    case 'ack':
      // Delivery confirmed — no UI action needed
      break;
    case 'typing':
      typingEl.classList.toggle('hidden', !frame.is_typing);
      break;
    case 'presence':
      appendSystemMsg(frame.status === 'online' ? 'Peer connected.' : 'Peer disconnected.');
      if (frame.status === 'offline') {
        peerPublicKey = null;
        peerFpEl.textContent = 'Peer disconnected';
        inputMsg.disabled = true;
        btnSend.disabled = true;
      }
      break;
  }
}

async function handleKeyExchange(armor) {
  try {
    peerPublicKey = await openpgp.readKey({ armoredKey: armor });
    const fp = peerPublicKey.getFingerprint();
    peerFingerprint = fp.toUpperCase().match(/.{1,4}/g).join(' ');
    peerFpEl.textContent = peerFingerprint;
    inputMsg.disabled = false;
    btnSend.disabled = false;
    appendSystemMsg('Peer key received. Verify fingerprint out-of-band before trusting.');
  } catch {
    appendSystemMsg('Warning: received invalid peer key.');
  }
}

async function handleChat(frame) {
  if (!privateKey) return;
  try {
    const msg = await openpgp.readMessage({ armoredMessage: frame.ciphertext });
    const { data: plaintext } = await openpgp.decrypt({
      message: msg,
      decryptionKeys: privateKey,
    });
    appendMessage(String(plaintext), false);
  } catch {
    appendMessage('[Could not decrypt message]', false);
  }
}

// ── Send message ──────────────────────────────────────────────────────────────
formSend.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = inputMsg.value.trim();
  if (!text || !peerPublicKey || !ws || ws.readyState !== WebSocket.OPEN) return;

  try {
    const encrypted = await openpgp.encrypt({
      message: await openpgp.createMessage({ text }),
      encryptionKeys: peerPublicKey,
    });

    ws.send(JSON.stringify({
      type: 'chat',
      session_id: sessionId,
      sender_fingerprint: myFingerprint.replace(/\s/g, ''),
      ciphertext: encrypted,
      timestamp: new Date().toISOString(),
    }));

    appendMessage(text, true);
    inputMsg.value = '';
  } catch (err) {
    console.error('Encrypt/send error:', err);
  }
});

// Typing indicator — debounced
let typingTimeout = null;
inputMsg.addEventListener('input', () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'typing', is_typing: true }));
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => {
      if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'typing', is_typing: false }));
    }, 1500);
  }
});

// ── End session ───────────────────────────────────────────────────────────────
btnEnd.addEventListener('click', async () => {
  if (ws) ws.close();
  if (sessionId) {
    try {
      await fetch(`/session/${sessionId}`, { method: 'DELETE' });
    } catch { /* best effort */ }
  }
  // Discard all key material
  privateKey = null;
  publicKeyArmor = '';
  peerPublicKey = null;
  sessionId = '';
  showSetup();
});

// Discard key material on tab close
window.addEventListener('beforeunload', () => {
  privateKey = null;
  publicKeyArmor = '';
  peerPublicKey = null;
  if (ws) ws.close();
});

// ── UI helpers ────────────────────────────────────────────────────────────────
function showChat(fullSessionId) {
  screenSetup.classList.add('hidden');
  screenChat.classList.remove('hidden');
  // Show the full session_id so the creator can copy and share it
  sessionDisplay.innerHTML = '';
  const label = document.createElement('span');
  label.textContent = 'Share ID: ';
  label.style.color = 'var(--muted)';
  const code = document.createElement('code');
  code.textContent = fullSessionId;
  code.style.cssText = 'font-size:.75rem;word-break:break-all;cursor:pointer;color:var(--accent);';
  code.title = 'Click to copy';
  code.addEventListener('click', () => {
    navigator.clipboard.writeText(fullSessionId).then(() => {
      code.textContent = 'Copied!';
      setTimeout(() => { code.textContent = fullSessionId; }, 1500);
    });
  });
  sessionDisplay.appendChild(label);
  sessionDisplay.appendChild(code);
}

function showSetup() {
  screenChat.classList.add('hidden');
  screenSetup.classList.remove('hidden');
  messagesEl.innerHTML = '';
  peerFpEl.textContent = 'Waiting for peer…';
  inputMsg.disabled = true;
  btnSend.disabled = true;
}

function setStatus(state) {
  statusDot.className = `dot ${state}`;
  statusDot.title = state.charAt(0).toUpperCase() + state.slice(1);
}

function appendMessage(text, mine) {
  const div = document.createElement('div');
  div.className = `msg ${mine ? 'mine' : 'theirs'}`;
  div.textContent = text;
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = new Date().toLocaleTimeString();
  div.appendChild(meta);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendSystemMsg(text) {
  const div = document.createElement('div');
  div.className = 'msg system';
  div.style.cssText = 'align-self:center;color:#888;font-size:.8rem;background:none;';
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
