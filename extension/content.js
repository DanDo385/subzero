// Subzero — content script.
// Runs in each provider tab. Detects the provider, listens for page changes,
// and extracts conversation data on demand (user click or auto-capture).

(function () {
  'use strict';

  // Prevent double-injection.
  if (window.__subzeroLoaded) return;
  window.__subzeroLoaded = true;

  /**
   * Detect which provider we're on.
   */
  function detectProvider() {
    const host = window.location.hostname;
    if (host.includes('claude.ai')) return 'claude';
    if (host.includes('chatgpt.com')) return 'chatgpt';
    if (host.includes('gemini.google.com')) return 'gemini';
    if (host.includes('grok.com')) return 'grok';
    if (host.includes('muse.ai')) return 'muse';
    return null;
  }

  const provider = detectProvider();
  if (!provider) return;

  console.log(`[PersonalContext] Detected provider: ${provider}`);

  // --- Message extraction (called on demand) ---

  /**
   * Extract the current conversation and send to the service worker for queuing.
   */
  async function captureCurrent() {
    try {
      const mod = (globalThis.__subzeroProviders || {})[provider];
      if (!mod) throw new Error(`No provider module registered for ${provider}`);
      const data = await mod.extractCurrentConversation();

      if (data && data.messages && data.messages.length > 0) {
        chrome.runtime.sendMessage({
          action: 'capture',
          payload: data,
        });
        console.log(`[PersonalContext] Captured ${data.messages.length} messages from ${data.title || 'untitled'}`);
      }
    } catch (err) {
      console.error('[PersonalContext] Capture failed:', err);
      chrome.runtime.sendMessage({
        action: 'captureError',
        error: err.message,
        provider,
      });
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

    const convs = await mod.listConversations();
    if (dryRun) {
      return { ok: true, dryRun: true, provider, conversations: convs.length, items: convs };
    }

    let captured = 0;
    let skipped = 0;

    if (mod.fetchConversation) {
      for (const conv of convs) {
        try {
          const data = await mod.fetchConversation(conv.id || conv.uuid);
          if (data) {
            chrome.runtime.sendMessage({ action: 'capture', payload: data });
            captured++;
          }
        } catch (err) {
          console.error(`[PersonalContext] Backfill error for ${conv.id || conv.uuid}:`, err);
          skipped++;
        }
        // Be gentle: one request per 2 seconds.
        await new Promise((r) => setTimeout(r, 2000));
      }
    } else {
      // DOM-only provider: only the open conversation is reachable.
      try {
        const data = await mod.extractCurrentConversation();
        if (data && data.messages && data.messages.length > 0) {
          chrome.runtime.sendMessage({ action: 'capture', payload: data });
          captured = 1;
        }
      } catch (err) {
        skipped = 1;
      }
    }

    return { ok: true, dryRun: false, provider, captured, skipped, total: convs.length };
  }

  // --- Message listener: respond to popup/service worker requests ---

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request.action === 'capture') {
      captureCurrent().then(() => sendResponse({ ok: true }));
      return true; // async response
    }
    if (request.action === 'backfill') {
      runBackfill(request.dryRun !== false).then(sendResponse);
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

  console.log('[PersonalContext] Content script ready');
})();