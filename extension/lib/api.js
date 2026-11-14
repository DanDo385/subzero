// Subzero — HTTPS client for the paired server.
//
// A device token belongs to the server (and database) that issued it, so the
// extension talks to exactly one server: the address entered when pairing.
// There is deliberately no fallback to another address: silently sending a
// conversation to a different server would put it in a different database.

const REQUEST_TIMEOUT_MS = 15000;

/** Error carrying the HTTP status, so callers can tell retryable failures apart. */
class ServerError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ServerError';
    this.status = status;
  }
}

/**
 * Normalise a server address typed by a person: add https://, drop paths.
 * Plain http is accepted only for this computer (localhost / 127.0.0.1).
 */
function normalizeServerUrl(input) {
  let text = String(input || '').trim();
  if (!text) throw new Error('Enter your Subzero server address.');
  if (!/^[a-z]+:\/\//i.test(text)) text = `https://${text}`;
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new Error(`"${input}" is not a valid address.`);
  }
  const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    throw new Error('The server address must start with https:// (http:// only for localhost).');
  }
  return url.origin;
}

/** The host-permission pattern the extension needs to reach a server. */
function originPattern(serverUrl) {
  const url = new URL(serverUrl);
  return `${url.protocol}//${url.hostname}/*`;
}

async function getServerUrl() {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  return serverUrl || null;
}

async function getDeviceToken() {
  const { deviceToken } = await chrome.storage.local.get('deviceToken');
  return deviceToken || null;
}

/** Paired means a token and the server that issued it are both stored. */
async function isPaired() {
  const { deviceToken, serverUrl } = await chrome.storage.local.get(['deviceToken', 'serverUrl']);
  return Boolean(deviceToken && serverUrl);
}

async function forgetPairing() {
  await chrome.storage.local.remove(['deviceToken', 'deviceId']);
  await chrome.storage.local.set({ paired: false });
}

/** fetch with a timeout and a readable message for network failures. */
async function fetchServer(serverUrl, path, init = {}) {
  try {
    return await fetch(`${serverUrl}${path}`, {
      ...init,
      signal: init.signal || AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (err) {
    const reason = err?.name === 'TimeoutError' ? 'timed out' : (err?.message || 'network error');
    throw new ServerError(`Could not reach ${serverUrl} (${reason}). Is the server running?`, 0);
  }
}

/**
 * Authenticated request to the paired server. A 401 means the server no longer
 * knows this token: forget it so the popup asks to pair again.
 */
async function authedFetch(path, init = {}) {
  const { deviceToken, serverUrl } = await chrome.storage.local.get(['deviceToken', 'serverUrl']);
  if (!deviceToken || !serverUrl) {
    throw new ServerError('This browser is not paired. Open Subzero and pair it.', 401);
  }
  const resp = await fetchServer(serverUrl, path, {
    ...init,
    headers: { ...(init.headers || {}), Authorization: `Bearer ${deviceToken}` },
  });
  if (resp.status === 401) {
    await forgetPairing();
    throw new ServerError(`${serverUrl} did not accept this browser's token. Pair it again.`, 401);
  }
  return resp;
}

async function errorText(resp) {
  const text = await resp.text().catch(() => '');
  try {
    return JSON.parse(text).error || text;
  } catch {
    return text;
  }
}

/**
 * Pair this browser with the server at serverUrl using a one-time code from
 * `subzero pair-device`. Stores the token together with that server address.
 */
async function pairDevice(serverUrlInput, pairingCode) {
  const serverUrl = normalizeServerUrl(serverUrlInput);
  const deviceName = `browser-${navigator.userAgent.slice(0, 60)}`;
  const resp = await fetchServer(serverUrl, '/api/v1/auth/pair-device', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pairing_code: pairingCode, device_name: deviceName }),
  });
  if (!resp.ok) {
    const detail = await errorText(resp);
    if (resp.status === 403) {
      throw new ServerError(
        `${serverUrl} does not know that code (${detail}). Codes last 5 minutes and must come `
        + 'from `subzero pair-device` run with this server\'s settings.',
        403,
      );
    }
    throw new ServerError(`Pairing failed: ${resp.status} ${detail}`, resp.status);
  }
  const { device_token, device_id } = await resp.json();
  await chrome.storage.local.set({
    serverUrl,
    deviceToken: device_token,
    deviceId: device_id,
    paired: true,
  });
  return { serverUrl, deviceId: device_id };
}

/**
 * Send one conversation to the ingest endpoint. Resolves only on a server
 * confirmation that names the stored conversation.
 */
async function ingestConversation(payload) {
  const resp = await authedFetch('/api/v1/ingest/conversation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    throw new ServerError(`Server refused the conversation: ${resp.status} ${await errorText(resp)}`, resp.status);
  }
  const result = await resp.json();
  if (!result.conversation_id) {
    throw new ServerError('Server returned no conversation confirmation', 502);
  }
  return { ...result, serverUrl: new URL(resp.url).origin };
}

/**
 * What the server already has for a provider:
 * { count, latest, conversations: { providerConversationId: providerUpdatedAt|null } }.
 */
async function getSyncState(provider) {
  const resp = await authedFetch(`/api/v1/ingest/sync-state?provider=${encodeURIComponent(provider)}`);
  if (!resp.ok) {
    throw new ServerError(`Could not read sync state: ${resp.status} ${await errorText(resp)}`, resp.status);
  }
  return resp.json();
}

async function fetchSession(sessionId) {
  const resp = await authedFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
  if (!resp.ok) {
    throw new ServerError(`Fetch session failed: ${resp.status} ${await errorText(resp)}`, resp.status);
  }
  return resp.json();
}

async function sendSessionMessage(sessionId, text) {
  const resp = await authedFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) {
    throw new ServerError(`Send message failed: ${resp.status} ${await errorText(resp)}`, resp.status);
  }
  return resp.json();
}

export {
  ServerError, normalizeServerUrl, originPattern, getServerUrl, getDeviceToken, isPaired,
  forgetPairing, pairDevice, ingestConversation, getSyncState, fetchSession, sendSessionMessage,
  authedFetch,
};
