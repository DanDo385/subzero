// Subzero — popup: pairing, delivery status per provider, imports.

import { isPaired, pairDevice, normalizeServerUrl, originPattern, forgetPairing } from '../lib/api.js';

const providers = ['claude', 'chatgpt', 'gemini', 'grok', 'muse'];
const providerNames = {
  claude: 'Claude', chatgpt: 'ChatGPT', gemini: 'Gemini', grok: 'Grok', muse: 'Muse',
};
const providerUrls = {
  claude: 'https://claude.ai',
  chatgpt: 'https://chatgpt.com',
  gemini: 'https://gemini.google.com',
  grok: 'https://grok.com',
  muse: 'https://muse.ai',
};
// An import whose tab stopped reporting for this long was interrupted.
const STALE_IMPORT_MS = 2 * 60 * 1000;

const $ = (id) => document.getElementById(id);

function showNotice(text, kind = '') {
  const el = $('notice');
  el.textContent = text;
  el.className = `notice ${kind}`;
}

function time(value) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ---------------------------------------------------------------- pairing

async function renderPairing() {
  $('pairSection').classList.remove('hidden');
  $('mainSection').classList.add('hidden');

  const { pairDraft = {}, serverUrl } = await chrome.storage.local.get(['pairDraft', 'serverUrl']);
  $('serverInput').value = pairDraft.server || serverUrl || '';
  $('pairInput').value = pairDraft.code || '';
  ($('serverInput').value ? $('pairInput') : $('serverInput')).focus();

  // Keep what was typed: the browser's permission prompt can close the popup.
  const saveDraft = () => chrome.storage.local.set({
    pairDraft: { server: $('serverInput').value, code: $('pairInput').value },
  });
  $('serverInput').oninput = saveDraft;
  $('pairInput').oninput = saveDraft;

  const fail = (message) => {
    $('pairError').textContent = message;
    $('pairError').classList.remove('hidden');
    $('pairBtn').disabled = false;
    $('pairBtn').textContent = 'Pair this browser';
  };

  const doPair = async () => {
    $('pairError').classList.add('hidden');
    let server;
    try {
      server = normalizeServerUrl($('serverInput').value);
    } catch (err) {
      fail(err.message);
      return;
    }
    const code = $('pairInput').value.trim().toUpperCase();
    if (code.length !== 8) {
      fail('Enter the 8-character code.');
      return;
    }

    // Must be the first await: the browser only shows the prompt during the click.
    let granted = false;
    try {
      granted = await chrome.permissions.request({ origins: [originPattern(server)] });
    } catch (err) {
      fail(`Could not ask for access to ${server}: ${err.message}`);
      return;
    }
    if (!granted) {
      fail(`Subzero needs permission to talk to ${server}. Click Pair again and choose Allow.`);
      return;
    }

    $('pairBtn').disabled = true;
    $('pairBtn').textContent = 'Pairing...';
    try {
      await pairDevice(server, code);
      await chrome.storage.local.remove('pairDraft');
      // Anything captured before pairing (or under an old token) can go now.
      chrome.runtime.sendMessage({ action: 'drain' });
      render();
    } catch (err) {
      fail(err.message);
    }
  };

  $('pairBtn').onclick = doPair;
  $('pairInput').onkeydown = (e) => { if (e.key === 'Enter') doPair(); };
}

// ---------------------------------------------------------------- status

function importLine(progress) {
  if (!progress) return null;
  const stale = progress.state === 'running' && Date.now() - (progress.heartbeatAt || 0) > STALE_IMPORT_MS;
  const counts = `${progress.queued} sent to queue, ${progress.unchanged} unchanged`
    + (progress.failed ? `, ${progress.failed} failed` : '');
  if (stale) {
    return {
      text: `Import stopped at ${progress.done}/${progress.toFetch} (tab closed?). Import again to resume.`,
      kind: 'error',
    };
  }
  if (progress.state === 'running') {
    return { text: `Importing ${progress.done}/${progress.toFetch} of ${progress.total}: ${counts}`, kind: 'working' };
  }
  if (progress.state === 'error') {
    return { text: `Import failed: ${progress.lastError}`, kind: 'error' };
  }
  return {
    text: `Imported ${progress.total} at ${time(progress.finishedAt)}: ${counts}`
      + (progress.failed && progress.lastError ? ` (${progress.lastError})` : ''),
    kind: progress.failed ? 'error' : 'success',
  };
}

function statusLine(text, kind = '') {
  const el = document.createElement('div');
  el.className = `provider-status ${kind}`;
  el.textContent = text;
  return el;
}

async function renderMain() {
  $('pairSection').classList.add('hidden');
  $('mainSection').classList.remove('hidden');

  const status = await chrome.runtime.sendMessage({ action: 'getStatus' });
  const progressKeys = providers.map((p) => `backfill:${p}`);
  const progress = await chrome.storage.local.get(progressKeys);
  const {
    lastDelivery = {}, deliveryErrors = {}, isPaused, deadLength, queueLength, lastFailure, serverUrl,
  } = status;

  $('serverLabel').textContent = serverUrl;

  const container = $('providers');
  container.innerHTML = '';
  for (const provider of providers) {
    const row = document.createElement('div');
    row.className = 'provider';
    const info = document.createElement('div');
    info.className = 'provider-info';

    const name = document.createElement('div');
    name.className = 'provider-name';
    name.textContent = providerNames[provider];
    info.appendChild(name);

    const last = lastDelivery[provider];
    if (last) {
      const short = last.title && last.title.length > 40 ? `${last.title.slice(0, 39).trimEnd()}…` : last.title;
      const title = short ? `"${short}"` : 'a conversation';
      info.appendChild(statusLine(
        `Saved ${title} (${last.messages ?? '?'} messages, ${last.messagesInserted} new) at ${time(last.at)}`,
        'success',
      ));
    } else {
      info.appendChild(statusLine('Nothing confirmed by the server yet'));
    }
    if (deliveryErrors[provider]) info.appendChild(statusLine(deliveryErrors[provider], 'error'));
    const line = importLine(progress[`backfill:${provider}`]);
    if (line) info.appendChild(statusLine(line.text, line.kind));

    const btn = document.createElement('button');
    btn.className = 'backfill-btn';
    btn.dataset.provider = provider;
    btn.textContent = 'Import';
    btn.title = provider === 'claude' || provider === 'chatgpt'
      ? 'Import your whole history from this site'
      : 'Save the conversation open in this site\'s tab (history import is not available)';
    btn.onclick = () => startImport(provider, btn);

    row.appendChild(info);
    row.appendChild(btn);
    container.appendChild(row);
  }

  const queueInfo = $('queueInfo');
  queueInfo.innerHTML = '';
  queueInfo.className = 'queue hidden';
  if (queueLength > 0) {
    queueInfo.appendChild(document.createTextNode(`${queueLength} waiting to send. `));
    queueInfo.classList.remove('hidden');
  }
  if (deadLength > 0) {
    queueInfo.appendChild(document.createTextNode(
      `${deadLength} could not be sent${lastFailure ? ` (${lastFailure})` : ''}.`,
    ));
    const retry = document.createElement('button');
    retry.className = 'link-btn';
    retry.textContent = 'Retry';
    retry.onclick = async () => {
      const result = await chrome.runtime.sendMessage({ action: 'retryFailed' });
      showNotice(`Retrying ${result.count} item(s).`);
    };
    queueInfo.appendChild(retry);
    queueInfo.className = 'queue error';
  }

  $('pauseBtn').textContent = isPaused ? 'Resume' : 'Pause';
  $('pauseBtn').className = isPaused ? 'pause-btn paused' : 'pause-btn';
  $('pauseBtn').onclick = async () => {
    await chrome.runtime.sendMessage({ action: 'setPaused', paused: !isPaused });
  };

  $('captureBtn').onclick = async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) return;
    $('captureBtn').disabled = true;
    showNotice('Reading this conversation...');
    const result = await chrome.runtime.sendMessage({ action: 'captureTab', tabId: tab.id });
    $('captureBtn').disabled = false;
    if (result?.ok) {
      showNotice(`Queued ${result.messages} messages. Sending to the server...`, 'success');
    } else {
      showNotice(result?.error || 'Capture failed', 'error');
    }
  };

  $('openAllBtn').onclick = async () => {
    for (const provider of providers) {
      if (!(await findTab(provider))) {
        await chrome.tabs.create({ url: providerUrls[provider], active: false });
      }
    }
  };

  $('backfillAllBtn').onclick = async () => {
    $('backfillAllBtn').disabled = true;
    for (const provider of providers) {
      const btn = container.querySelector(`button[data-provider="${provider}"]`);
      await startImport(provider, btn, { openTab: true });
    }
    $('backfillAllBtn').disabled = false;
  };

  $('unpairBtn').onclick = async () => {
    await forgetPairing();
    render();
  };
}

async function findTab(provider) {
  const host = new URL(providerUrls[provider]).hostname;
  const tabs = await chrome.tabs.query({});
  return tabs.find((t) => {
    try { return new URL(t.url || '').hostname === host; }
    catch { return false; }
  });
}

function waitForLoad(tabId) {
  return new Promise((resolve) => {
    const done = () => {
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve();
    };
    const listener = (id, change) => { if (id === tabId && change.status === 'complete') done(); };
    const timer = setTimeout(done, 20000);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function startImport(provider, btn, { openTab = false } = {}) {
  btn.disabled = true;
  try {
    if (!(await findTab(provider))) {
      if (!openTab) {
        showNotice(`Open ${providerNames[provider]} in a tab, sign in, then click Import.`, 'error');
        return;
      }
      const tab = await chrome.tabs.create({ url: providerUrls[provider], active: false });
      await waitForLoad(tab.id);
    }
    showNotice(`${providerNames[provider]}: listing conversations...`);
    const result = await chrome.runtime.sendMessage({ action: 'backfill', provider });
    if (!result?.ok) {
      showNotice(`${providerNames[provider]}: ${result?.error || 'import failed'}`, 'error');
    } else if (result.scope === 'current') {
      showNotice(`${providerNames[provider]}: saved the open conversation (history import is not available for this site).`, 'success');
    } else if (result.running) {
      showNotice(`${providerNames[provider]}: an import is already running.`);
    } else {
      showNotice(
        `${providerNames[provider]}: ${result.total} conversations, ${result.toFetch} new or changed. `
        + 'Keep the tab open until the import finishes.',
        'success',
      );
    }
  } catch (err) {
    showNotice(`${providerNames[provider]}: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- render

async function render() {
  if (await isPaired()) await renderMain();
  else await renderPairing();
}

let pending = null;
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area !== 'local' || $('pairSection').offsetParent) return; // don't wipe a half-typed form
  clearTimeout(pending);
  pending = setTimeout(render, 250);
});

document.addEventListener('DOMContentLoaded', render);
