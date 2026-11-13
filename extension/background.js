// Subzero — service worker.
// Handles capture queue processing, periodic sync alarms, and backfill coordination.

import { enqueue, processOne, getStatus } from './lib/queue.js';
import { sendToProviderTab } from './lib/tabs.js';

// Alarm names.
const PROCESS_ALARM = 'processQueue';
const SYNC_ALARM = 'periodicSync';

// --- Lifecycle ---

chrome.runtime.onInstalled.addListener(() => {
  console.log('[subzero] Extension installed');

  // Create alarm for queue processing (every 30s when items exist).
  chrome.alarms.create(PROCESS_ALARM, { periodInMinutes: 0.5 });

  // Create alarm for periodic sync (every 15 minutes).
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: 15 });

  // Set default server URL.
  chrome.storage.local.get('serverUrl', (result) => {
    if (!result.serverUrl) {
      chrome.storage.local.set({
        serverUrl: 'https://mbp.tailfbd394.ts.net',
        fallbackUrl: '',
      });
    }
  });
});

// --- Alarm handler ---

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === PROCESS_ALARM) {
    const { isPaused } = await chrome.storage.local.get('isPaused');
    if (isPaused) return;

    // Process up to 5 items per alarm tick.
    for (let i = 0; i < 5; i++) {
      const processed = await processOne();
      if (!processed) break;
      // Small delay between items to avoid overwhelming the server.
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  if (alarm.name === SYNC_ALARM) {
    const { isPaused } = await chrome.storage.local.get('isPaused');
    if (isPaused) return;

    // Trigger capture in all open provider tabs.
    const tabs = await chrome.tabs.query({});
    const providerHosts = [
      'claude.ai', 'chatgpt.com',
      'gemini.google.com', 'grok.com', 'muse.ai',
    ];

    for (const tab of tabs) {
      try {
        const url = new URL(tab.url || '');
        if (providerHosts.some((h) => url.hostname === h)) {
          sendToProviderTab(tab.id, { action: 'capture' }).catch(() => {
            // Tab may not have content script loaded — ignore.
          });
        }
      } catch { /* skip malformed URLs */ }
    }
  }
});

// --- Message handler ---

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'capture') {
    const payload = request.payload;
    // Tag with timestamp.
    payload._capturedAt = new Date().toISOString();
    enqueue(payload).then(() => {
      sendResponse({ ok: true });
    }).catch((err) => {
      console.error('[subzero] Enqueue failed:', err);
      sendResponse({ ok: false, error: err.message });
    });
    return true; // async
  }

  if (request.action === 'captureError') {
    console.error(`[subzero] Capture error on ${request.provider}:`, request.error);
    sendResponse({ ok: true });
    return false;
  }

  if (request.action === 'getStatus') {
    getStatus().then(sendResponse);
    return true;
  }

  if (request.action === 'setPaused') {
    chrome.storage.local.set({ isPaused: request.paused });
    sendResponse({ ok: true });
    return false;
  }

  if (request.action === 'captureTab') {
    sendToProviderTab(request.tabId, { action: 'capture' })
      .then(sendResponse)
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (request.action === 'backfill') {
    handleBackfill(request.provider, request.dryRun)
      .then(sendResponse)
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});

// --- Backfill ---

async function handleBackfill(provider, dryRun = true) {
  // Find a tab for this provider.
  const providerHosts = {
    claude: 'claude.ai',
    chatgpt: 'chatgpt.com',
    gemini: 'gemini.google.com',
    grok: 'grok.com',
    muse: 'muse.ai',
  };

  const host = providerHosts[provider];
  if (!host) throw new Error(`Unknown provider: ${provider}`);

  const tabs = await chrome.tabs.query({});
  const tab = tabs.find((t) => {
    try { return new URL(t.url || '').hostname === host; }
    catch { return false; }
  });

  if (!tab) throw new Error(`No ${provider} tab open`);

  // The content script owns extraction: it runs in the page's isolated world
  // with provider modules loaded as classic scripts. Reconnect older tabs first.
  const result = await sendToProviderTab(tab.id, {
    action: 'backfill',
    dryRun,
  });

  return result || { ok: false, error: 'No result from tab' };
}

console.log('[subzero] Service worker ready');
