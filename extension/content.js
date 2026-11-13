// Subzero — content script.
// Runs in each provider tab. Detects the provider, captures the open
// conversation on demand or after a reply finishes, and runs history imports.

(function () {
  'use strict';

  const HOSTS = {
    'claude.ai': 'claude',
    'chatgpt.com': 'chatgpt',
    'gemini.google.com': 'gemini',
    'grok.com': 'grok',
    'muse.ai': 'muse',
  };
  const provider = HOSTS[window.location.hostname] || null;
  if (!provider) return;

  // Each injection takes over from any earlier one in this page. After the
  // extension is reloaded, the old copy is orphaned (its chrome.runtime is
  // dead) but can still share this world; it must not keep observers running
  // or block the new copy from registering its listener.
  const instance = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  window.__subzeroInstance = instance;
  const isCurrent = () => window.__subzeroInstance === instance;

  const IMPORT_DELAY_MS = 1000;
  const RATE_LIMIT_WAIT_MS = 30000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  console.log(`[subzero] Detected provider: ${provider}`);

  function providerModule() {
    const mod = (globalThis.__subzeroProviders || {})[provider];
    if (!mod) throw new Error(`No provider module registered for ${provider}. Refresh this tab.`);
    return mod;
  }

  /** Hand a capture to the service worker and wait until it is persisted. */
  async function queueCapture(data) {
    if (data.provider !== provider) throw new Error('Provider mismatch. Refresh this tab.');
    if (!data.provider_conversation_id) {
      throw new Error('No conversation ID found. Open a saved conversation and try again.');
    }
    const response = await chrome.runtime.sendMessage({ action: 'capture', payload: data });
    if (!response?.ok) throw new Error(response?.error || 'Could not queue conversation');
  }

  // What the last capture from this tab contained, to skip identical re-captures.
  let lastFingerprint = null;
  function fingerprint(data) {
    const last = data.messages[data.messages.length - 1] || {};
    return [
      data.provider_conversation_id, data.messages.length,
      last.provider_message_id || '', (last.content || '').length,
    ].join('|');
  }

  /**
   * Capture the open conversation. Automatic captures skip a conversation
   * that has not changed since the last one from this tab.
   */
  async function captureCurrent({ automatic = false } = {}) {
    try {
      const data = await providerModule().extractCurrentConversation();
      if (!data || !data.messages || data.messages.length === 0) {
        return { ok: false, error: 'No messages found. Open a conversation and try again.' };
      }
      const print = fingerprint(data);
      if (automatic && print === lastFingerprint) return { ok: true, captured: 0, unchanged: true };
      await queueCapture(data);
      lastFingerprint = print;
      return {
        ok: true,
        captured: 1,
        provider,
        conversationId: data.provider_conversation_id,
        messages: data.messages.length,
      };
    } catch (err) {
      console.error('[subzero] Capture failed:', err);
      return { ok: false, error: err.message };
    }
  }

  // --- History import ---

  const progressKey = `backfill:${provider}`;
  let importRunning = false;

  async function saveProgress(progress) {
    await chrome.storage.local.set({ [progressKey]: { ...progress, heartbeatAt: Date.now() } });
  }

  function toEpoch(value) {
    if (value == null || value === '') return NaN;
    if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
    return Date.parse(value);
  }

  /** Conversations the server already confirmed at this version are skipped. */
  async function changedSinceDelivery(convs) {
    const { delivered = {} } = await chrome.storage.local.get('delivered');
    return convs.filter((conv) => {
      const confirmed = delivered[`${provider}:${conv.id || conv.uuid}`];
      const updated = toEpoch(conv.updated_at ?? conv.update_time);
      return !(Number.isFinite(confirmed) && Number.isFinite(updated)
        && Math.abs(updated - confirmed) < 1000);
    });
  }

  async function fetchWithRateLimit(mod, id) {
    let wait = RATE_LIMIT_WAIT_MS;
    for (let attempt = 0; ; attempt++) {
      try {
        return await mod.fetchConversation(id);
      } catch (err) {
        if (err.status !== 429 || attempt >= 4) throw err;
        await sleep(err.retryAfterMs || wait);
        wait *= 2;
      }
    }
  }

  async function importAll(mod, convs, progress) {
    for (const conv of convs) {
      if (!isCurrent()) return;
      const id = conv.id || conv.uuid;
      try {
        const data = await fetchWithRateLimit(mod, id);
        if (data?.messages?.length) {
          await queueCapture(data);
          progress.queued++;
        } else {
          progress.empty++;
        }
      } catch (err) {
        console.error(`[subzero] Import failed for ${id}:`, err);
        progress.failed++;
        progress.lastError = err.message;
      }
      progress.done++;
      await saveProgress(progress);
      await sleep(IMPORT_DELAY_MS);
    }
    progress.state = 'done';
    progress.finishedAt = Date.now();
    await saveProgress(progress);
  }

  /**
   * Start a history import. Replies once the conversation list is known (or
   * listing failed); fetching runs on in this tab and writes progress to
   * storage for the popup. Providers without an API (DOM-only) can reach only
   * the open conversation, and say so.
   */
  async function startImport() {
    const mod = providerModule();
    if (importRunning) return { ok: true, provider, running: true };

    if (!mod.fetchConversation) {
      const result = await captureCurrent();
      return { ...result, provider, scope: 'current' };
    }

    const all = await mod.listConversations();
    const todo = await changedSinceDelivery(all);
    const progress = {
      state: 'running', scope: 'history', startedAt: Date.now(),
      total: all.length, unchanged: all.length - todo.length, toFetch: todo.length,
      done: 0, queued: 0, empty: 0, failed: 0, lastError: null,
    };
    await saveProgress(progress);
    importRunning = true;
    importAll(mod, todo, progress)
      .catch(async (err) => {
        progress.state = 'error';
        progress.lastError = err.message;
        await saveProgress(progress);
      })
      .finally(() => { importRunning = false; });
    return { ok: true, provider, scope: 'history', started: true, ...progress };
  }

  // --- Messages from the popup and service worker ---

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (!isCurrent()) return false;
    if (request.action === 'ping') {
      sendResponse({ provider, url: window.location.href, instance });
      return false;
    }
    if (request.action === 'capture') {
      captureCurrent({ automatic: Boolean(request.automatic) }).then(sendResponse);
      return true;
    }
    if (request.action === 'backfill') {
      startImport().then(sendResponse).catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;
    }
    return false;
  });

  // --- Auto-capture after a reply finishes or the conversation changes ---

  function replyJustFinished() {
    switch (provider) {
      case 'claude':
        return !document.querySelector('[data-is-streaming="true"]');
      case 'chatgpt':
        return !document.querySelector('.result-streaming, [data-testid="stop-button"]');
      case 'gemini':
        return !document.querySelector('processing-state');
      default:
        // Grok and Muse: no reliable streaming signal; the debounce stands in.
        return true;
    }
  }

  let debounceTimer = null;
  function scheduleAutoCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      if (!isCurrent()) return;
      try {
        const { isPaused } = await chrome.storage.local.get('isPaused');
        if (isPaused || !replyJustFinished()) return;
        await captureCurrent({ automatic: true });
      } catch {
        // chrome.* throws once this copy is orphaned by an extension reload.
        observer.disconnect();
      }
    }, 3000);
  }

  let lastUrl = window.location.href;
  const observer = new MutationObserver(() => {
    if (!isCurrent()) {
      observer.disconnect();
      return;
    }
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      lastFingerprint = null;
    }
    scheduleAutoCapture();
  });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });

  console.log('[subzero] Content script ready');
})();
