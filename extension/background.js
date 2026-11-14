// Subzero — service worker.
// Owns the capture queue and its delivery, the periodic alarms, and routing
// popup requests to provider tabs.

import { enqueue, drain, retryFailed, getStatus } from './lib/queue.js';
import { sendToProviderTab, PROVIDER_HOSTS } from './lib/tabs.js';
import { getSyncState, isPaired } from './lib/api.js';

const PROCESS_ALARM = 'processQueue';
const SYNC_ALARM = 'periodicSync';
// Keep the database current: every 2 minutes, each open Claude/ChatGPT tab
// fetches what changed since the server last saw it. When nothing changed
// this is one small list request per tab plus one to the server.
const SYNC_MINUTES = 2;
// Providers whose whole history can be listed (the others save the open page).
const HISTORY_PROVIDERS = new Set(['claude', 'chatgpt']);

function ensureAlarms() {
  // Delivery retries (the queue applies its own per-item backoff).
  chrome.alarms.create(PROCESS_ALARM, { periodInMinutes: 0.5 });
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_MINUTES });
}

chrome.runtime.onInstalled.addListener(() => {
  console.log('[subzero] Extension installed or updated');
  ensureAlarms();
  // Settings from before 0.2 that no longer mean anything.
  chrome.storage.local.remove(['fallbackUrl', 'lastSync']);
});

chrome.runtime.onStartup.addListener(() => {
  ensureAlarms();
  drain();
});

/** Every open tab per provider (first one wins). */
async function providerTabs() {
  const found = new Map();
  for (const tab of await chrome.tabs.query({})) {
    try {
      const provider = PROVIDER_HOSTS[new URL(tab.url || '').hostname];
      if (provider && !found.has(provider)) found.set(provider, tab);
    } catch { /* malformed URL */ }
  }
  return found;
}

/** Start a sync ('sync') or full import ('backfill') in a provider's tab. */
async function startInTab(provider, tab, action) {
  let known = {};
  if (HISTORY_PROVIDERS.has(provider)) {
    // Fetch only what the database does not already have. Without the
    // server's answer a sync is pointless; an import falls back to what this
    // browser has delivered.
    try {
      known = (await getSyncState(provider)).conversations || {};
    } catch (err) {
      if (action === 'sync') throw err;
    }
  }
  return sendToProviderTab(tab.id, { action, known });
}

async function periodicSync() {
  const { isPaused } = await chrome.storage.local.get('isPaused');
  if (isPaused || !(await isPaired())) return;
  for (const [provider, tab] of await providerTabs()) {
    const work = HISTORY_PROVIDERS.has(provider)
      ? startInTab(provider, tab, 'sync')
      : sendToProviderTab(tab.id, { action: 'capture', automatic: true });
    // A tab still loading, signed out or without access is simply skipped.
    work.catch((err) => console.debug(`[subzero] sync skipped for ${provider}:`, err.message));
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === PROCESS_ALARM) await drain();
  if (alarm.name === SYNC_ALARM) await periodicSync();
});

async function tabFor(provider) {
  if (!Object.values(PROVIDER_HOSTS).includes(provider)) {
    throw new Error(`Unknown provider: ${provider}`);
  }
  const tab = (await providerTabs()).get(provider);
  if (!tab) throw new Error(`Open ${provider} in a tab and sign in first.`);
  return tab;
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  const reply = (promise) => {
    promise.then(sendResponse).catch((err) => sendResponse({ ok: false, error: err.message }));
    return true; // async response
  };

  switch (request.action) {
    case 'capture': {
      const payload = { ...request.payload, _capturedAt: new Date().toISOString() };
      return reply(enqueue(payload).then((info) => {
        drain(); // deliver now; do not make the tab wait for the server
        return { ok: true, ...info };
      }));
    }
    case 'captureTab':
      return reply(sendToProviderTab(request.tabId, { action: 'capture' }));
    case 'backfill':
      return reply(tabFor(request.provider).then((tab) => startInTab(request.provider, tab, 'backfill')));
    case 'syncNow':
      return reply(periodicSync().then(() => ({ ok: true })));
    case 'stopImport':
      return reply(tabFor(request.provider).then((tab) =>
        chrome.tabs.sendMessage(tab.id, { action: 'stopImport' })));
    case 'getStatus':
      return reply(getStatus());
    case 'drain':
      return reply(drain().then(() => getStatus()));
    case 'retryFailed':
      return reply(retryFailed().then((count) => {
        drain();
        return { ok: true, count };
      }));
    case 'setPaused':
      return reply(chrome.storage.local.set({ isPaused: Boolean(request.paused) }).then(() => {
        if (!request.paused) drain();
        return { ok: true };
      }));
    default:
      return false;
  }
});

console.log('[subzero] Service worker ready');
