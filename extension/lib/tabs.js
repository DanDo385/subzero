// Recover capture scripts in tabs that were open before installation/reload.
export async function sendToProviderTab(tabId, message) {
  const tab = await chrome.tabs.get(tabId);
  const url = new URL(tab.url || '');
  const scripts = chrome.runtime.getManifest().content_scripts.find((entry) =>
    entry.matches.includes(`${url.origin}/*`)
  );
  if (!scripts) throw new Error('Open a supported chat page and try again.');

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
      throw new Error(`Refresh the ${url.hostname} tab and allow Subzero site access. ${err.message}`);
    }
  }

  // Send the operation once. Retrying a long import could duplicate the job.
  return chrome.tabs.sendMessage(tabId, message);
}
