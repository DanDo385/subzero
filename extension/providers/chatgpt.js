(function () {
  'use strict';

// Subzero — ChatGPT provider extractor.
// Uses ChatGPT's internal backend API (same endpoints the web app loads).
// Auth: Bearer token from /api/auth/session.

const CHATGPT_SESSION_API = 'https://chatgpt.com/api/auth/session';
const CHATGPT_LIST_API = 'https://chatgpt.com/backend-api/conversations';
const CHATGPT_CONVERSATION_API = (id) =>
  `https://chatgpt.com/backend-api/conversation/${id}`;

let cachedToken = null;

/** Error with the HTTP status, so the importer can back off on 429. */
function httpError(message, resp) {
  const err = new Error(`${message}: ${resp.status}`);
  err.status = resp.status;
  const retryAfter = Number(resp.headers?.get?.('retry-after'));
  if (Number.isFinite(retryAfter) && retryAfter > 0) err.retryAfterMs = retryAfter * 1000;
  return err;
}

/**
 * Get the Bearer token from the session endpoint.
 */
async function getToken(refresh = false) {
  if (cachedToken && !refresh) return cachedToken;
  const resp = await fetch(CHATGPT_SESSION_API, { credentials: 'include' });
  if (!resp.ok) throw httpError('Failed to get ChatGPT session', resp);
  const session = await resp.json();
  cachedToken = session.accessToken || null;
  if (!cachedToken) throw new Error('Not signed in to ChatGPT in this tab');
  return cachedToken;
}

/** GET a backend-api URL, refreshing the access token once if it expired. */
async function apiGet(url, label) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getToken(attempt > 0);
    const resp = await fetch(url, {
      credentials: 'include',
      headers: { 'Authorization': `Bearer ${token}`, 'Accept': 'application/json' },
    });
    if (resp.status === 401 && attempt === 0) continue;
    if (!resp.ok) throw httpError(label, resp);
    return resp.json();
  }
  throw new Error(`${label}: unauthorized`);
}

/**
 * List all conversations with pagination.
 * Returns [{ id, title, create_time, update_time }].
 */
async function listConversations() {
  const all = [];
  const seen = new Set();
  const limit = 100;
  for (let offset = 0; ; ) {
    const url = `${CHATGPT_LIST_API}?offset=${offset}&limit=${limit}&order=updated`;
    const data = await apiGet(url, `Failed to list conversations at offset ${offset}`);
    const items = data.items || [];
    for (const item of items) {
      if (item?.id && !seen.has(item.id)) {
        seen.add(item.id);
        all.push(item);
      }
    }
    offset += items.length;
    if (items.length === 0) break;
    if (Number.isFinite(data.total) ? offset >= data.total : items.length < limit) break;
  }
  return all;
}

/** Text of a message's content, whatever its content_type. */
function messageText(content) {
  if (Array.isArray(content.parts)) {
    return content.parts
      .map((p) => (typeof p === 'string' ? p : p?.text || ''))
      .filter(Boolean)
      .join('\n');
  }
  if (typeof content.text === 'string') return content.text;
  if (Array.isArray(content.thoughts)) {
    return content.thoughts.map((t) => `[thinking] ${t.content || ''}`).join('\n\n');
  }
  return '';
}

/**
 * Walk the message tree from current_node to root.
 * Returns active-lineage messages in order.
 */
function walkActiveLineage(mapping, currentNodeId) {
  const messages = [];
  let current = currentNodeId;

  while (current && mapping[current]) {
    const node = mapping[current];
    const msg = node.message;
    const hidden = msg?.metadata?.is_visually_hidden_from_conversation
      || msg?.content?.content_type === 'user_editable_context';
    const content = msg?.content ? messageText(msg.content).trim() : '';
    if (msg && msg.author && content && !hidden) {
      const role = msg.author.role;
      messages.unshift({
        provider_message_id: msg.id,
        role: role === 'tool' ? 'tool' : role,
        content,
        created_at: msg.create_time ? new Date(msg.create_time * 1000).toISOString() : null,
        model: msg.metadata?.model_slug || null,
      });
    }
    current = node.parent;
  }

  return messages;
}

/**
 * Fetch one full conversation.
 */
async function fetchConversation(conversationId) {
  const raw = await apiGet(
    CHATGPT_CONVERSATION_API(conversationId),
    `Failed to fetch conversation ${conversationId}`,
  );
  const messages = walkActiveLineage(raw.mapping || {}, raw.current_node);

  // Extract attachments from message metadata.
  const attachments = [];
  for (const nodeId of Object.keys(raw.mapping || {})) {
    const node = raw.mapping[nodeId];
    for (const att of node.message?.metadata?.attachments || []) {
      attachments.push({
        provider_attachment_id: att.id,
        file_name: att.name,
        file_type: att.mime_type,
        file_size: att.size,
      });
    }
  }

  return {
    provider: 'chatgpt',
    provider_conversation_id: raw.conversation_id || conversationId,
    title: raw.title || '',
    model: raw.default_model_slug || null,
    created_at: raw.create_time
      ? new Date(raw.create_time * 1000).toISOString()
      : null,
    updated_at: raw.update_time
      ? new Date(raw.update_time * 1000).toISOString()
      : null,
    messages,
    attachments,
  };
}

/**
 * Extract the currently open conversation from the page context.
 */
async function extractCurrentConversation() {
  const url = new URL(window.location.href);
  const match = url.pathname.match(/\/c\/([a-zA-Z0-9-]+)/);
  if (!match) return null;

  return fetchConversation(match[1]);
}

// Registered for the content script: MV3 content scripts are classic scripts
// and cannot import ES modules, so providers attach to a shared global.
(globalThis.__subzeroProviders ||= {}).chatgpt = {
  getToken, listConversations, fetchConversation, extractCurrentConversation,
};

})();
