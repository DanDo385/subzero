// Subzero — side panel script.
// Tab 1: Project handoff from the server.
// Tab 2: Session bridge — connect to a CLI session and exchange messages.

import {
  isPaired, fetchSession, sendSessionMessage,
} from '../lib/api.js';

// ---- Tab switching ----

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.tab;
    document.getElementById('handoffTab').classList.toggle('hidden', target !== 'handoff');
    document.getElementById('bridgeTab').classList.toggle('hidden', target !== 'bridge');
  });
});

// ---- Handoff tab ----

async function initHandoff() {
  try {
    const { serverUrl, deviceToken } = await chrome.storage.local.get([
      'serverUrl', 'deviceToken',
    ]);

    if (!deviceToken) {
      document.getElementById('handoff').textContent =
        'Not authenticated. Open the extension popup first.';
      return;
    }

    const resp = await fetch(
      `${serverUrl || 'https://mbp.tailfbd394.ts.net'}/api/v1/handoff`,
      { headers: { 'Authorization': `Bearer ${deviceToken}` } }
    );

    if (!resp.ok) {
      document.getElementById('handoff').textContent =
        `Could not load handoff: ${resp.status}`;
      return;
    }

    const data = await resp.json();
    document.getElementById('handoff').textContent =
      data.handoff || data.text || JSON.stringify(data, null, 2);
  } catch (err) {
    document.getElementById('handoff').textContent = `Error: ${err.message}`;
  }
}

document.getElementById('insertBtn').onclick = async () => {
  const text = document.getElementById('handoff').textContent;
  await navigator.clipboard.writeText(text);
  const btn = document.getElementById('insertBtn');
  btn.textContent = 'Copied!';
  setTimeout(() => { btn.textContent = 'Copy to Clipboard'; }, 1500);
};

// ---- Session Bridge tab ----

let currentSessionId = null;
let pollTimer = null;

/**
 * Extract a session ID from a link or raw code.
 * Accepts plain UUIDs, subzero://session/<id>, or full URLs.
 */
function extractSessionId(input) {
  const trimmed = input.trim();
  // Plain UUID or short code.
  if (/^[a-zA-Z0-9_-]{4,64}$/.test(trimmed)) return trimmed;
  // subzero:// protocol or https:// URL with session path.
  const urlMatch = trimmed.match(/[?&/]session[s]?\/([a-zA-Z0-9_-]+)/);
  if (urlMatch) return urlMatch[1];
  // Try parsing as URL and look for a session path segment.
  try {
    const url = new URL(trimmed);
    const parts = url.pathname.split('/').filter(Boolean);
    const sessIdx = parts.findIndex((p) => p === 's' || p === 'session');
    if (sessIdx >= 0 && parts[sessIdx + 1]) return parts[sessIdx + 1];
  } catch { /* not a URL */ }
  return null;
}

function setBridgeStatus(text, isError = false) {
  const el = document.getElementById('bridgeStatus');
  el.textContent = text;
  el.className = 'status-bar' + (isError ? ' error' : '');
}

function renderMessages(messages) {
  const container = document.getElementById('bridgeMessages');
  container.innerHTML = '';

  if (!messages || messages.length === 0) {
    container.innerHTML = '<div class="message system">No messages yet.</div>';
    return;
  }

  for (const msg of messages) {
    const div = document.createElement('div');
    div.className = `message ${msg.role || 'user'}`;

    let meta = '';
    if (msg.created_at) {
      const d = new Date(msg.created_at);
      meta = `<div class="meta">${d.toLocaleString()}</div>`;
    }
    if (msg.model && msg.role === 'assistant') {
      meta = `<div class="meta">${msg.model} · ${new Date(msg.created_at).toLocaleString()}</div>`;
    }

    div.innerHTML = meta + escapeHtml(msg.content || msg.text || '');
    container.appendChild(div);
  }

  // Scroll to bottom.
  container.scrollTop = container.scrollHeight;
}

function escapeHtml(str) {
  const el = document.createElement('span');
  el.textContent = str;
  return el.innerHTML;
}

async function connectSession() {
  const input = document.getElementById('sessionIdInput').value;
  const sessionId = extractSessionId(input);

  if (!sessionId) {
    setBridgeStatus('Invalid session code. Paste a full link or the session ID.', true);
    return;
  }

  // Clear any existing poll.
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }

  setBridgeStatus('Connecting...');
  currentSessionId = sessionId;

  try {
    const paired = await isPaired();
    if (!paired) {
      setBridgeStatus('Not paired. Use the extension popup to pair first.', true);
      return;
    }

    await loadSession();
    document.getElementById('replyRow').classList.remove('hidden');

    // Poll for new messages every 5 seconds.
    pollTimer = setInterval(async () => {
      try {
        const data = await fetchSession(currentSessionId);
        renderMessages(data.messages || []);
        setBridgeStatus(`Connected · ${data.provider || 'session'} · ${data.title || 'untitled'}`);
      } catch (err) {
        // Ignore poll errors — keep showing last known state.
      }
    }, 5000);
  } catch (err) {
    setBridgeStatus(err.message, true);
    currentSessionId = null;
  }
}

async function loadSession() {
  const data = await fetchSession(currentSessionId);
  renderMessages(data.messages || []);
  setBridgeStatus(`Connected · ${data.provider || 'session'} · ${data.title || 'untitled'}`);
}

async function sendReply() {
  const input = document.getElementById('replyInput');
  const text = input.value.trim();
  if (!text || !currentSessionId) return;

  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  btn.textContent = 'Sending...';

  try {
    await sendSessionMessage(currentSessionId, text);
    input.value = '';
    btn.textContent = 'Sent!';
    // Refresh messages immediately.
    await loadSession();
  } catch (err) {
    setBridgeStatus(err.message, true);
  } finally {
    btn.disabled = false;
    setTimeout(() => { btn.textContent = 'Send'; }, 1500);
  }
}

// Connect button.
document.getElementById('connectBtn').onclick = connectSession;

// Enter key on session ID input.
document.getElementById('sessionIdInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') connectSession();
});

// Send button.
document.getElementById('sendBtn').onclick = sendReply;

// Enable/disable send button based on input.
document.getElementById('replyInput').addEventListener('input', (e) => {
  document.getElementById('sendBtn').disabled = !e.target.value.trim();
});

// Enter key on reply input.
document.getElementById('replyInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) sendReply();
});

// ---- Init ----

document.addEventListener('DOMContentLoaded', () => {
  initHandoff();
});