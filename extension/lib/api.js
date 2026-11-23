// Subzero — HTTPS ingest client.
// Sends conversations to the server. Never auto-registers on 401;
// the user must pair the browser with a code from the server CLI.
//
// Server resolution: the stored serverUrl wins, then the local dev server,
// then the Tailscale and Vercel addresses. A network-level failure (refused,
// DNS, TLS) falls through to the next candidate; HTTP errors do not.

const DEFAULT_SERVER_URL = 'http://localhost:8080';
const FALLBACK_SERVER_URLS = [
  'https://mbp.tailfbd394.ts.net',
  'https://subzero-eta.vercel.app',
];

/**
 * Ordered list of server bases to try, without duplicates.
 */
async function serverCandidates() {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  const list = [];
  if (serverUrl) list.push(serverUrl.replace(/\/+$/, ''));
  for (const url of [DEFAULT_SERVER_URL, ...FALLBACK_SERVER_URLS]) {
    if (!list.includes(url)) list.push(url);
  }
  return list;
}

/**
 * Fetch a path, trying each candidate server until one answers.
 * Only network-level failures fall through; HTTP statuses are returned as-is.
 */
async function fetchWithServers(path, init = {}) {
  const candidates = await serverCandidates();
  let lastErr = null;
  for (const base of candidates) {
    try {
      return await fetch(`${base}${path}`, init);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error('No Subzero server reachable');
}

/**
 * Get the configured server URL, with fallback.
 */
async function getServerUrl() {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  return serverUrl || DEFAULT_SERVER_URL;
}

/**
 * Get the stored device token.
 */
async function getDeviceToken() {
  const { deviceToken } = await chrome.storage.local.get('deviceToken');
  return deviceToken || null;
}

/**
 * Check if this browser is paired (has a valid token).
 */
async function isPaired() {
  const token = await getDeviceToken();
  return token !== null;
}

/**
 * Pair this browser using a one-time code from `subzero pair-device`.
 * Returns the device token on success.
 */
async function pairDevice(pairingCode) {
  const deviceName = `browser-${navigator.userAgent.slice(0, 60)}`;
  const resp = await fetchWithServers('/api/v1/auth/pair-device', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      pairing_code: pairingCode,
      device_name: deviceName,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error || `Pairing failed: ${resp.status}`);
  }
  const { device_token, device_id } = await resp.json();
  await chrome.storage.local.set({
    deviceToken: device_token,
    deviceId: device_id,
    paired: true,
  });
  return device_token;
}

/**
 * Send a single conversation to the ingest endpoint.
 * Returns { conversation_id, status, messages_inserted, messages_skipped }.
 * Throws on 401 (not paired) — never auto-registers.
 */
async function ingestConversation(payload) {
  const token = await getDeviceToken();

  if (!token) {
    throw new Error('Browser not paired. Use "Pair this browser" in the popup.');
  }

  const body = JSON.stringify(payload);
  const resp = await fetchWithServers('/api/v1/ingest/conversation', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body,
  });

  if (resp.status === 401) {
    // Token expired or revoked — clear it and tell the user.
    await chrome.storage.local.remove(['deviceToken', 'deviceId']);
    await chrome.storage.local.set({ paired: false });
    throw new Error('Device token expired or revoked. Re-pair the browser.');
  }

  if (!resp.ok) {
    throw new Error(`Ingest failed: ${resp.status} ${await resp.text()}`);
  }

  return resp.json();
}

/**
 * Fetch a session's full message history by session ID.
 * Returns { session_id, provider, title, model, messages, ... }.
 */
async function fetchSession(sessionId) {
  const token = await getDeviceToken();

  if (!token) {
    throw new Error('Browser not paired. Use "Pair this browser" in the popup.');
  }

  const resp = await fetchWithServers(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}`,
    { headers: { 'Authorization': `Bearer ${token}` } },
  );

  if (resp.status === 401) {
    await chrome.storage.local.remove(['deviceToken', 'deviceId']);
    await chrome.storage.local.set({ paired: false });
    throw new Error('Device token expired or revoked. Re-pair the browser.');
  }

  if (!resp.ok) {
    throw new Error(`Fetch session failed: ${resp.status} ${await resp.text()}`);
  }

  return resp.json();
}

/**
 * Send a message into an active session bridge.
 * Returns { message_id, status, ... }.
 */
async function sendSessionMessage(sessionId, text) {
  const token = await getDeviceToken();

  if (!token) {
    throw new Error('Browser not paired. Use "Pair this browser" in the popup.');
  }

  const resp = await fetchWithServers(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ text }),
    },
  );

  if (resp.status === 401) {
    await chrome.storage.local.remove(['deviceToken', 'deviceId']);
    await chrome.storage.local.set({ paired: false });
    throw new Error('Device token expired or revoked. Re-pair the browser.');
  }

  if (!resp.ok) {
    throw new Error(`Send message failed: ${resp.status} ${await resp.text()}`);
  }

  return resp.json();
}

export {
  getServerUrl, getDeviceToken, isPaired, pairDevice, ingestConversation,
  fetchSession, sendSessionMessage, fetchWithServers,
};