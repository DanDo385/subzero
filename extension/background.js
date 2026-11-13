// Subzero — service worker.
// Owns the capture queue and its delivery, the periodic alarms, and routing
// popup requests to provider tabs.

import { enqueue, drain, retryFailed, getStatus } from './lib/queue.js';
import { sendToProviderTab, PROVIDER_HOSTS } from './lib/tabs.js';

const PROCESS_ALARM = 'processQueue';
const SYNC_ALARM = 'periodicSync';

function ensureAlarms() {
  // Delivery retries (the queue applies its own per-item backoff).
  chrome.alarms.create(PROCESS_ALARM, { periodInMinutes: 0.5 });
  // Re-capture open provider tabs now and then.
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 15 });
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

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === PROCESS_ALARM) {
    await drain();
    return;
  }
  if (alarm.name === SYNC_ALARM) {
    const { isPaused } = await chrome.storage.local.get('isPaused');
    if (isPaused) return;
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      try {
        if (!PROVIDER_HOSTS[new URL(tab.url || '').hostname]) continue;
        // Tabs that cannot be reached (still loading, no access) are skipped.
        sendToProviderTab(tab.id, { action: 'capture', automatic: true }).catch(() => {});
      } catch { /* malformed URL */ }
    }
  }
});

/** Find (but never open) a tab for a provider. */
async function findProviderTab(provider) {
  const tabs = await chrome.tabs.query({});
  return tabs.find((t) => {
    try { return PROVIDER_HOSTS[new URL(t.url || '').hostname] === provider; }
    catch { return false; }
  });
}

async function handleBackfill(provider) {
  if (!Object.values(PROVIDER_HOSTS).includes(provider)) {
    throw new Error(`Unknown provider: ${provider}`);
  }
  const tab = await findProviderTab(provider);
  if (!tab) throw new Error(`Open ${provider} in a tab and sign in first.`);
  const result = await sendToProviderTab(tab.id, { action: 'backfill' });
  return result || { ok: false, error: 'No answer from the tab. Refresh it and try again.' };
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
      return reply(handleBackfill(request.provider));
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
