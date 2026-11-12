// Subzero — Muse (Meta) provider extractor.
// DOM-based extraction from muse.ai chat interface.
// Muse is a React-based SPA; selectors may need iteration after live testing.

/**
 * Detect if we're on a Muse conversation page.
 */
function isConversationPage() {
  // Muse chat pages typically have a conversation ID in the path.
  const hasChatPath = /\/chat\/|conversation/i.test(window.location.pathname);
  // Or the main chat interface at the root.
  const isRoot = window.location.pathname === '/' || window.location.pathname === '';
  if (!hasChatPath && !isRoot) return false;

  // Look for message containers — Muse renders chat messages in a thread.
  return document.querySelector('[data-testid="message"], [class*="message"], [role="log"]') !== null;
}

/**
 * Scroll the chat container to load all messages.
 * Muse lazy-loads older messages as you scroll up.
 */
async function scrollToLoadAll() {
  const scroller = document.querySelector(
    '[class*="overflow-y-auto"]'
  ) || document.querySelector('[class*="scroll"]')
    || document.querySelector('main');

  if (!scroller) return;

  let prevHeight = 0;
  for (let i = 0; i < 20; i++) {
    scroller.scrollTop = 0;
    await new Promise((r) => setTimeout(r, 500));
    const newHeight = scroller.scrollHeight;
    if (newHeight === prevHeight) break;
    prevHeight = newHeight;
  }
}

/**
 * Detect the role of a message container.
 * Muse uses distinct styles for user vs assistant messages:
 * - User messages are right-aligned or have a different background.
 * - Assistant messages are left-aligned with the Muse avatar.
 */
function detectRole(container) {
  const signals = { user: 0, assistant: 0 };
  const classes = (container.className || '').toLowerCase();
  const html = container.innerHTML || '';

  // Signal 1: Alignment classes.
  if (classes.includes('justify-end') || classes.includes('self-end') ||
      classes.includes('right') || classes.includes('user')) {
    signals.user += 2;
  }
  if (classes.includes('justify-start') || classes.includes('self-start') ||
      classes.includes('left') || classes.includes('assistant') ||
      classes.includes('bot')) {
    signals.assistant += 2;
  }

  // Signal 2: Data attributes.
  const role = container.getAttribute('data-role') ||
    container.getAttribute('data-sender') ||
    container.getAttribute('data-message-role');
  if (role === 'user' || role === 'human') signals.user += 3;
  if (role === 'assistant' || role === 'ai' || role === 'bot') signals.assistant += 3;

  // Signal 3: Avatar presence (assistant messages often have an avatar).
  if (container.querySelector('img[alt*="Muse"], img[src*="avatar"], [class*="avatar"]')) {
    signals.assistant += 1;
  }

  // Signal 4: Text content heuristics (assistant messages often have longer responses).
  const text = container.textContent || '';
  if (text.length > 500) signals.assistant += 1;
  if (text.length < 200 && !classes.includes('assistant') && !classes.includes('bot')) signals.user += 1;

  if (signals.user > signals.assistant) return 'user';
  if (signals.assistant > signals.user) return 'assistant';
  return null;
}

/**
 * Extract text content from a message container.
 */
function extractContent(container) {
  // Try common markdown/content selectors.
  const selectors = [
    '[class*="prose"]', '[class*="markdown"]', '[class*="content"]',
    '[class*="message-text"]', '[class*="message-content"]',
    'p', 'span',
  ];

  for (const sel of selectors) {
    const el = container.querySelector(sel);
    if (el && el.textContent?.trim().length > 5) {
      return el.textContent.trim();
    }
  }

  // Fallback: clone and remove non-content elements.
  const clone = container.cloneNode(true);
  clone.querySelectorAll('button, nav, [class*="toolbar"], [class*="actions"], [class*="avatar"]')
    .forEach((el) => el.remove());
  return clone.textContent?.trim() || '';
}

/**
 * Extract the currently open conversation from the DOM.
 */
async function extractCurrentConversation() {
  if (!isConversationPage()) return null;

  await scrollToLoadAll();

  // Find all message containers.
  const containers = Array.from(
    document.querySelectorAll(
      '[data-testid="message"], [class*="message-row"], [class*="message-container"], [role="article"]'
    )
  ).filter((c) => {
    const text = c.textContent?.trim() || '';
    return text.length > 10; // Skip empty containers.
  });

  if (containers.length === 0) return null;

  // Extract conversation ID from URL.
  const url = new URL(window.location.href);
  const idMatch = url.pathname.match(/\/([a-f0-9-]{20,})\b/);
  const convId = idMatch ? idMatch[1] : '';

  // Extract title from page.
  const titleEl = document.querySelector('h1') ||
    document.querySelector('[class*="title"]') ||
    document.querySelector('title');
  const title = titleEl?.textContent?.trim() || '';

  // Extract messages.
  const messages = [];
  for (const container of containers) {
    const role = detectRole(container);
    if (!role) continue;

    const content = extractContent(container);
    if (!content) continue;

    // Generate a stable ID from content hash.
    const hash = Array.from(new Uint8Array(
      new TextEncoder().encode(content.slice(0, 50))
    )).reduce((h, b) => (h * 31 + b) & 0xfffffff, 0);

    messages.push({
      provider_message_id: `muse-msg-${hash.toString(16)}`,
      role,
      content,
      created_at: null,
      model: null,
    });
  }

  return {
    provider: 'muse',
    provider_conversation_id: convId,
    title,
    model: null,
    messages,
  };
}

/**
 * List conversations from the sidebar (for backfill).
 * Muse sidebar renders a virtual-scroll list; extract what's visible.
 */
async function listConversations() {
  // Try to find conversation list items in the sidebar.
  const items = document.querySelectorAll(
    '[class*="conversation"], [class*="chat-item"], [class*="thread"], [class*="history-item"], [role="listitem"] a'
  );

  const conversations = [];
  const seen = new Set();

  for (const item of items) {
    const href = item.href || item.getAttribute('href') || '';
    const name = item.textContent?.trim() || '';
    const idMatch = href.match(/\/([a-f0-9-]{20,})\b/);

    if (!idMatch || seen.has(idMatch[1])) continue;
    seen.add(idMatch[1]);

    conversations.push({
      uuid: idMatch[1],
      name: name || 'Untitled',
      model: null,
      created_at: null,
      updated_at: null,
    });
  }

  return conversations;
}

export { extractCurrentConversation, listConversations, isConversationPage };