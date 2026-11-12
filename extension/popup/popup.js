// Subzero — popup script.
// Shows sync status per provider, pause toggle, pairing flow, and backfill controls.

import { isPaired, pairDevice } from '../lib/api.js';

const providers = ['claude', 'chatgpt', 'gemini', 'grok', 'muse'];
const providerNames = {
  claude: 'Claude',
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  grok: 'Grok',
  muse: 'Muse',
};
const providerHosts = {
  claude: 'https://claude.ai',
  chatgpt: 'https://chatgpt.com',
  gemini: 'https://gemini.google.com',
  grok: 'https://grok.com',
  muse: 'https://muse.ai',
};

let isBackfilling = false;

async function init() {
  const paired = await isPaired();

  if (!paired) {
    document.getElementById('pairSection').classList.remove('hidden');
    document.getElementById('mainSection').classList.add('hidden');

    const pairInput = document.getElementById('pairInput');
    const pairBtn = document.getElementById('pairBtn');
    const pairError = document.getElementById('pairError');

    pairInput.focus();

    const doPair = async () => {
      const code = pairInput.value.trim().toUpperCase();
      if (code.length !== 8) {
        pairError.textContent = 'Enter the 8-character code.';
        pairError.classList.remove('hidden');
        return;
      }

      pairBtn.disabled = true;
      pairBtn.innerHTML = '<span class="pair-spinner"></span>Pairing...';
      pairError.classList.add('hidden');

      try {
        await pairDevice(code);
        init();
      } catch (err) {
        pairError.textContent = err.message;
        pairError.classList.remove('hidden');
        pairBtn.disabled = false;
        pairBtn.textContent = 'Pair this browser';
      }
    };

    pairBtn.onclick = doPair;
    pairInput.onkeydown = (e) => { if (e.key === 'Enter') doPair(); };
    return;
  }

  // Paired — show main section.
  document.getElementById('pairSection').classList.add('hidden');
  document.getElementById('mainSection').classList.remove('hidden');

  // Get status from service worker.
  const status = await chrome.runtime.sendMessage({ action: 'getStatus' });
  const { lastSync = {}, isPaused, deadLength, queueLength } = status;

  // Get active backfill status.
  const { backfillActive = {}, backfillProgress = {} } =
    await chrome.storage.local.get(['backfillActive', 'backfillProgress']);

  // Render provider cards with backfill buttons.
  const container = document.getElementById('providers');
  container.innerHTML = '';

  for (const provider of providers) {
    const div = document.createElement('div');
    div.className = 'provider';

    const info = document.createElement('div');
    info.className = 'provider-info';

    const name = document.createElement('div');
    name.className = 'provider-name';
    name.textContent = providerNames[provider];

    const statusEl = document.createElement('div');
    const last = lastSync[provider];
    if (last) {
      const time = new Date(last).toLocaleTimeString();
      statusEl.textContent = `Last sync: ${time}`;
      statusEl.className = 'provider-status success';
    } else {
      statusEl.textContent = 'No sync yet';
      statusEl.className = 'provider-status';
    }

    info.appendChild(name);
    info.appendChild(statusEl);

    // Backfill button per provider.
    const btn = document.createElement('button');
    btn.className = 'backfill-btn';
    btn.dataset.provider = provider;

    if (backfillActive[provider]) {
      btn.textContent = 'Importing...';
      btn.className = 'backfill-btn working';
      btn.disabled = true;
    } else {
      btn.textContent = 'Import';
    }

    btn.onclick = () => startBackfill(provider, btn, statusEl);

    div.appendChild(info);
    div.appendChild(btn);
    container.appendChild(div);
  }

  if (queueLength > 0) {
    const queueInfo = document.createElement('div');
    queueInfo.className = 'provider-status';
    queueInfo.style.marginTop = '4px';
    queueInfo.textContent = `${queueLength} item(s) in queue`;
    container.appendChild(queueInfo);
  }

  // Show backfill progress bar if active.
  showBackfillProgress(backfillProgress, backfillActive);

  // Pause button.
  const pauseBtn = document.getElementById('pauseBtn');
  if (isPaused) {
    pauseBtn.textContent = 'Resume';
    pauseBtn.className = 'pause-btn paused';
  } else {
    pauseBtn.textContent = 'Pause';
    pauseBtn.className = 'pause-btn';
  }

  pauseBtn.onclick = async () => {
    await chrome.runtime.sendMessage({ action: 'setPaused', paused: !isPaused });
    init();
  };

  // Capture button.
  document.getElementById('captureBtn').onclick = async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      chrome.tabs.sendMessage(tab.id, { action: 'capture' }).catch(() => {
        alert('Could not capture. Make sure you are on a supported chat page.');
      });
      window.close();
    }
  };

  // Open All Tabs button.
  document.getElementById('openAllBtn').onclick = async () => {
    for (const provider of providers) {
      const host = providerHosts[provider];
      // Check if tab already open.
      const tabs = await chrome.tabs.query({});
      const existing = tabs.find((t) => {
        try { return new URL(t.url || '').hostname.includes(new URL(host).hostname); }
        catch { return false; }
      });
      if (!existing) {
        await chrome.tabs.create({ url: host, active: false });
      }
    }
    window.close();
  };

  // Backfill All button.
  document.getElementById('backfillAllBtn').onclick = async () => {
    if (isBackfilling) return;
    isBackfilling = true;
    document.getElementById('backfillAllBtn').disabled = true;

    // Open all provider tabs first.
    for (const provider of providers) {
      const host = providerHosts[provider];
      const tabs = await chrome.tabs.query({});
      const existing = tabs.find((t) => {
        try { return new URL(t.url || '').hostname.includes(new URL(host).hostname); }
        catch { return false; }
      });
      if (!existing) {
        await chrome.tabs.create({ url: host, active: false });
      }
    }

    // Wait a moment for tabs to start loading.
    await new Promise((r) => setTimeout(r, 2000));

    // Backfill each provider sequentially.
    for (const provider of providers) {
      try {
        await chrome.runtime.sendMessage({
          action: 'backfill',
          provider,
          dryRun: false,
        });
      } catch (err) {
        console.error(`[PersonalContext] Backfill ${provider} error:`, err);
      }
    }

    isBackfilling = false;
    init();
  };

  // Dead letter queue.
  if (deadLength > 0) {
    const dl = document.getElementById('deadLetter');
    dl.className = 'dead-letter';
    dl.textContent = `${deadLength} item(s) failed. Check the server.`;
  }
}

/**
 * Start a backfill for a single provider.
 */
async function startBackfill(provider, btn, statusEl) {
  if (isBackfilling) return;
  isBackfilling = true;

  btn.disabled = true;
  btn.textContent = 'Opening...';
  btn.className = 'backfill-btn working';
  statusEl.textContent = 'Opening provider tab...';
  statusEl.className = 'provider-status working';

  try {
    // Find or open the provider tab.
    const host = providerHosts[provider];
    const hostname = new URL(host).hostname;

    const tabs = await chrome.tabs.query({});
    let tab = tabs.find((t) => {
      try { return new URL(t.url || '').hostname.includes(hostname); }
      catch { return false; }
    });

    if (!tab) {
      tab = await chrome.tabs.create({ url: host, active: false });
      // Wait for the tab to load.
      await new Promise((resolve) => {
        const listener = (tabId, changeInfo) => {
          if (tabId === tab.id && changeInfo.status === 'complete') {
            chrome.tabs.onUpdated.removeListener(listener);
            resolve();
          }
        };
        chrome.tabs.onUpdated.addListener(listener);
        // Timeout after 15s.
        setTimeout(() => {
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }, 15000);
      });
    }

    btn.textContent = 'Importing...';
    statusEl.textContent = 'Listing conversations...';

    // Start backfill via service worker.
    const result = await chrome.runtime.sendMessage({
      action: 'backfill',
      provider,
      dryRun: false,
    });

    if (result && result.ok !== false) {
      const count = result.captured || result.result?.captured || 0;
      const total = result.conversations || result.result?.conversations || 0;
      statusEl.textContent = `Imported ${count} conversations`;
      statusEl.className = 'provider-status success';
    } else {
      statusEl.textContent = result?.error || 'Backfill failed';
      statusEl.className = 'provider-status error';
    }
  } catch (err) {
    statusEl.textContent = err.message || 'Error';
    statusEl.className = 'provider-status error';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import';
    btn.className = 'backfill-btn';
    isBackfilling = false;
  }
}

/**
 * Show backfill progress from storage.
 */
function showBackfillProgress(progress, active) {
  const progDiv = document.getElementById('backfillProgress');
  const fill = document.getElementById('progressFill');
  const label = document.getElementById('progressLabel');

  const activeProviders = Object.keys(active).filter((k) => active[k]);
  if (activeProviders.length === 0) {
    progDiv.classList.add('hidden');
    return;
  }

  progDiv.classList.remove('hidden');
  const pct = Math.min(100, (progress.done || 0) / Math.max(1, progress.total || 1) * 100);
  fill.style.width = `${pct}%`;
  label.textContent = `Importing from ${activeProviders.join(', ')}: ${progress.done || 0}/${progress.total || '?'}`;
}

document.addEventListener('DOMContentLoaded', init);