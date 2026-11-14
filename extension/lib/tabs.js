// Reach the capture script in a provider tab, injecting it when the tab was
// open before the extension was installed, updated or reloaded (Chrome only
// injects manifest content scripts into pages loaded afterwards).

export const PROVIDER_HOSTS = {
  'claude.ai': 'claude',
  'chatgpt.com': 'chatgpt',
  'gemini.google.com': 'gemini',
  'grok.com': 'grok',
  'muse.ai': 'muse',
};

export async function sendToProviderTab(tabId, message) {
  const tab = await chrome.tabs.get(tabId);
  let url;
  try {
    url = new URL(tab.url || '');
  } catch {
    throw new Error('Open a supported chat page and try again.');
  }
  const scripts = chrome.runtime.getManifest().content_scripts.find((entry) =>
    entry.matches.includes(`${url.origin}/*`)
  );
  if (!scripts || !PROVIDER_HOSTS[url.hostname]) {
    throw new Error('Open a supported chat page (Claude, ChatGPT, Gemini, Grok or Muse) and try again.');
  }

  try {
    await chrome.tabs.sendMessage(tabId, { action: 'ping' });
  } catch {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: scripts.js,
        world: 'ISOLATED',
      });
      await chrome.tabs.sendMessage(tabId, { action: 'ping' });
    } catch (err) {
      throw new Error(
        `Subzero cannot reach the ${url.hostname} tab. Refresh it, and check that Subzero `
        + `has site access to ${url.hostname} (${err.message}).`,
      );
    }
  }

  // Send the operation once: retrying a long import could start it twice.
  return chrome.tabs.sendMessage(tabId, message);
}
