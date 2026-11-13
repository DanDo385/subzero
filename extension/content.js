// Subzero — content script.
// Runs in each provider tab. Detects the provider, listens for page changes,
// and extracts conversation data on demand (user click or auto-capture).

(function () {
  'use strict';

  // Prevent double-injection.
  if (window.__subzeroLoaded) return;

  /**
   * Detect which provider we're on.
   */
  function detectProvider() {
    const host = window.location.hostname;
    if (host === 'claude.ai') return 'claude';
    if (host === 'chatgpt.com') return 'chatgpt';
    if (host === 'gemini.google.com') return 'gemini';
    if (host === 'grok.com') return 'grok';
    if (host === 'muse.ai') return 'muse';
    return null;
  }

  const provider = detectProvider();
  if (!provider) return;
  window.__subzeroLoaded = true;

  console.log(`[subzero] Detected provider: ${provider}`);

  // --- Message extraction (called on demand) ---

  /**
   * Wait for the service worker to persist a valid provider capture.
   */
  async function queueCapture(data) {
    if (data.provider !== provider) throw new Error('Provider mismatch. Refresh this tab.');
    if (!data.provider_conversation_id) {
      throw new Error('No conversation ID found. Open a saved conversation and try again.');
    }
    const response = await chrome.runtime.sendMessage({ action: 'capture', payload: data });
    if (!response?.ok) throw new Error(response?.error || 'Could not queue conversation');
  }

  async function captureCurrent() {
    try {
      const mod = (globalThis.__subzeroProviders || {})[provider];
      if (!mod) throw new Error(`No provider module registered for ${provider}`);
      const data = await mod.extractCurrentConversation();

      if (data && data.messages && data.messages.length > 0) {
        await queueCapture(data);
        return { ok: true, captured: 1 };
      } else {
        return { ok: false, error: 'No messages found. Open a conversation and try again.' };
      }
    } catch (err) {
      console.error('[subzero] Capture failed:', err);
      return { ok: false, error: err.message };
    }
  }

  /**
   * Backfill: list conversations and fetch each one in-page, sending captures
   * to the service worker as they arrive. Providers with fetchConversation
   * (API-based) walk their full history; DOM-only providers can only reach
   * the conversation currently open, and say so in the result.
   */
  async function runBackfill(dryRun = true) {
    const mod = (globalThis.__subzeroProviders || {})[provider];
    if (!mod) return { ok: false, error: `No provider module registered for ${provider}` };

    if (!mod.fetchConversation && !dryRun) {
      const result = await captureCurrent();
      return { ...result, provider, scope: 'current', dryRun: false };
    }

    const convs = await mod.listConversations();
    if (dryRun) {
      return { ok: true, dryRun: true, provider, conversations: convs.length, items: convs };
    }

    let captured = 0;
    let skipped = 0;
    let lastError = null;

    if (mod.fetchConversation) {
      for (const conv of convs) {
        try {
          const data = await mod.fetchConversation(conv.id || conv.uuid);
          if (data?.messages?.length) {
            await queueCapture(data);
            captured++;
          } else {
            skipped++;
          }
        } catch (err) {
          lastError = err.message;
          console.error(`[subzero] Backfill error for ${conv.id || conv.uuid}:`, err);
          skipped++;
        }
        // Be gentle: one request per 2 seconds.
        await new Promise((r) => setTimeout(r, 2000));
      }
    }

    return { ok: !lastError, error: lastError, dryRun: false, provider, captured, skipped, total: convs.length };
  }

  // --- Message listener: respond to popup/service worker requests ---

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === 'capture') {
      captureCurrent().then(sendResponse);
      return true; // async response
    }
    if (request.action === 'backfill') {
      runBackfill(request.dryRun !== false).then(sendResponse)
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true; // async response
    }
    if (request.action === 'ping') {
      sendResponse({ provider, url: window.location.href });
      return false;
    }
  });

  // --- Auto-capture: detect page changes (new conversation, finished reply) ---

  let lastUrl = window.location.href;
  let debounceTimer = null;

  /**
   * Check if a reply just finished (for auto-capture trigger).
   * Implementation varies by provider.
   */
  function replyJustFinished() {
    switch (provider) {
      case 'claude':
        // Claude: streaming indicator disappears.
        return !document.querySelector('[data-is-streaming="true"]');
      case 'chatgpt':
        // ChatGPT: result-streaming class removed.
        return !document.querySelector('.result-streaming');
      case 'gemini':
        // Gemini: processing-state element disappears.
        return !document.querySelector('processing-state');
      case 'grok':
        // Grok: check if message content has stabilized.
        // We use a simple heuristic: no new response- containers in the last 2s.
        return true; // Always consider "finished" — captures on timer.
      case 'muse':
        // Muse: streaming indicator not yet mapped — captures on timer.
        return true;
      default:
        return false;
    }
  }

  /**
   * Auto-capture: triggered when a reply finishes or URL changes.
   * Debounced to avoid capturing mid-stream.
   */
  function scheduleAutoCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      // Check pause state.
      const { isPaused } = await chrome.storage.local.get('isPaused');
      if (isPaused) return;

      if (replyJustFinished()) {
        captureCurrent();
      }
    }, 2000); // 2s debounce after last change.
  }

  // Watch for URL changes (new conversation opened).
  const urlObserver = new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      scheduleAutoCapture();
    }
  });

  // Watch for DOM changes (new messages, finished streaming).
  const domObserver = new MutationObserver(() => {
    scheduleAutoCapture();
  });

  // Start observers.
  urlObserver.observe(document.body, { childList: true, subtree: true });
  domObserver.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  console.log('[subzero] Content script ready');
})();
