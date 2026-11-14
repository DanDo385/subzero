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

  // Pause between conversation fetches (tests set the override to 0).
  const IMPORT_DELAY_MS = globalThis.__subzeroImportDelayMs ?? 1000;
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

  // --- History import and incremental sync ---
  //
  // Both walk the provider's conversation list newest first, one page at a
  // time, and fetch only conversations that changed since the server (or this
  // browser) last confirmed them. A full import walks every page; a sync stops
  // at the first conversation that is already up to date, so when nothing
  // changed it costs one small list request.

  const progressKey = `backfill:${provider}`;
  let running = null; // { mode, stop }
  const SYNC_PAGE = 20;
  const IMPORT_PAGE = 100;

  async function saveProgress(progress) {
    await chrome.storage.local.set({ [progressKey]: { ...progress, heartbeatAt: Date.now() } });
  }

  function toEpoch(value) {
    if (value == null || value === '') return NaN;
    if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
    return Date.parse(value);
  }

  /** Latest confirmed update time per conversation: the server's, or this browser's. */
  async function confirmedTimes(serverKnown) {
    const { delivered = {} } = await chrome.storage.local.get('delivered');
    return (id) => {
      const fromServer = toEpoch(serverKnown?.[id]);
      const fromHere = delivered[`${provider}:${id}`];
      const times = [fromServer, fromHere].filter(Number.isFinite);
      return times.length ? Math.max(...times) : NaN;
    };
  }

  function upToDate(conv, confirmed) {
    const seen = confirmed(conv.id || conv.uuid);
    const updated = toEpoch(conv.updated_at ?? conv.update_time);
    return Number.isFinite(seen) && Number.isFinite(updated) && updated <= seen + 1000;
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

  async function walk(mod, mode, serverKnown, job) {
    const full = mode === 'import';
    const confirmed = await confirmedTimes(serverKnown);
    const progress = {
      mode, state: 'running', startedAt: Date.now(), checked: 0, unchanged: 0,
      queued: 0, empty: 0, failed: 0, lastError: null,
    };
    await saveProgress(progress);
    let cursor;
    outer: for (;;) {
      const { items, next } = await mod.listPage(cursor, full ? IMPORT_PAGE : SYNC_PAGE);
      for (const conv of items) {
        if (job.stop || !isCurrent()) break outer;
        progress.checked++;
        if (upToDate(conv, confirmed)) {
          progress.unchanged++;
          if (!full) break outer; // everything older is up to date too
          continue;
        }
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
          console.error(`[subzero] ${mode} failed for ${id}:`, err);
          progress.failed++;
          progress.lastError = err.message;
        }
        await saveProgress(progress);
        await sleep(IMPORT_DELAY_MS);
      }
      if (!next) break;
      cursor = next;
    }
    progress.state = job.stop ? 'stopped' : 'done';
    progress.finishedAt = Date.now();
    await saveProgress(progress);
    return progress;
  }

  /**
   * Start an import ('import': every conversation) or a sync ('sync': only
   * what changed). Replies at once; the work continues in this tab and writes
   * progress to storage. Providers without an API save the open conversation.
   */
  async function start(mode, serverKnown) {
    const mod = providerModule();
    if (!mod.listPage || !mod.fetchConversation) {
      if (mode === 'sync') return { ok: true, provider, scope: 'current', skipped: true };
      const result = await captureCurrent();
      return { ...result, provider, scope: 'current' };
    }
    if (running) return { ok: true, provider, running: true, mode: running.mode };
    const job = { mode, stop: false };
    running = job;
    walk(mod, mode, serverKnown, job)
      .catch(async (err) => {
        await saveProgress({ mode, state: 'error', lastError: err.message, finishedAt: Date.now() });
      })
      .finally(() => { if (running === job) running = null; });
    return { ok: true, provider, scope: 'history', started: true, mode };
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
    if (request.action === 'backfill' || request.action === 'sync') {
      const mode = request.action === 'sync' ? 'sync' : 'import';
      start(mode, request.known).then(sendResponse)
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;
    }
    if (request.action === 'stopImport') {
      if (running) running.stop = true;
      sendResponse({ ok: true, provider, stopping: Boolean(running) });
      return false;
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
