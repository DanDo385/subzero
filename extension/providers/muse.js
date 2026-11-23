(function () {
  'use strict';

// Subzero — Muse provider extractor (DOM).
//
// Muse has one main chat at https://muse.ai/ and side chats at
// https://muse.ai/thread/<uuid>. Every message in the chat log is an element
// with data-message-item, carrying its own id and role:
//
//   <div data-message-item data-message-id="…" data-message-role="user|assistant">
//
// Muse virtualises long chats: a message scrolled out of view is replaced by
// an sr-only "search surrogate" holding its full text, prefixed with
// "User message: " or "Assistant message: ". Both forms are read, so the whole
// loaded chat is captured without scrolling. Muse shows no message times.

const MAIN_CHAT_ID = 'main';
const SURROGATE = '[data-message-search-surrogate], [data-message-accessibility-surrogate]';
const SURROGATE_PREFIX = /^(User|Assistant) message:\s*/;
// Rendered user bubbles start with a visible "You:" label.
const USER_LABEL = /^You:\s*/;

/** The open chat's id: a side chat's uuid, or MAIN_CHAT_ID for the main chat. */
function chatId() {
  const path = window.location.pathname;
  const side = path.match(/^\/thread\/([0-9a-f-]{20,})/i);
  if (side) return side[1];
  return path === '/' || path === '' ? MAIN_CHAT_ID : null;
}

function chatLog() {
  return document.querySelector('[role="log"][data-hatch-chat-column]')
    || document.querySelector('[role="log"]');
}

function isConversationPage() {
  return chatId() !== null && Boolean(chatLog()?.querySelector('[data-message-item]'));
}

/** A message element's text, whether rendered or virtualised. */
function messageText(item, role) {
  const surrogate = item.querySelector(SURROGATE);
  if (surrogate) return surrogate.textContent.replace(SURROGATE_PREFIX, '').trim();
  const body = item.querySelector('[data-hatch-assistant-message-body]') || item;
  let text = (body.innerText ?? body.textContent ?? '').trim();
  if (role === 'user') text = text.replace(USER_LABEL, '').trim();
  return text;
}

function chatTitle(id) {
  const label = document.querySelector('[data-testid="hatch-chat-switcher-trigger"] span.truncate');
  const text = label?.textContent?.trim();
  if (text) return text;
  return id === MAIN_CHAT_ID ? 'Muse main chat' : '';
}

async function extractCurrentConversation() {
  const id = chatId();
  const log = chatLog();
  if (!id || !log) return null;

  const messages = [];
  const seen = new Set();
  for (const item of log.querySelectorAll('[data-message-item]')) {
    const messageId = item.getAttribute('data-message-id');
    const role = item.getAttribute('data-message-role');
    if (!messageId || seen.has(messageId)) continue;
    if (role !== 'user' && role !== 'assistant') continue;
    const content = messageText(item, role);
    if (!content) continue;
    seen.add(messageId);
    messages.push({ provider_message_id: messageId, role, content, created_at: null, model: null });
  }
  if (!messages.length) return null;

  return {
    provider: 'muse',
    provider_conversation_id: id,
    title: chatTitle(id),
    model: null,
    messages,
  };
}

/** Muse has no history API the page can call; only the open chat is captured. */
async function listConversations() {
  return [];
}

// Registered for the content script: MV3 content scripts are classic scripts
// and cannot import ES modules, so providers attach to a shared global.
(globalThis.__subzeroProviders ||= {}).muse = {
  extractCurrentConversation, listConversations, isConversationPage,
};

})();
