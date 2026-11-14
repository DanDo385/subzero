(function () {
  'use strict';

// Subzero — Claude.ai provider extractor.
// Uses Claude's internal JSON API (same endpoints the web app loads).
// Auth: session cookies, auto-attached by the browser.

const CLAUDE_ORG_API = 'https://claude.ai/api/organizations';
const CLAUDE_CONVERSATIONS_API = (orgId) =>
  `https://claude.ai/api/organizations/${orgId}/chat_conversations`;
const CLAUDE_CONVERSATION_API = (orgId, convId) =>
  `https://claude.ai/api/organizations/${orgId}/chat_conversations/${convId}?tree=true&rendering_mode=messages&render_all_tools=true`;

let cachedOrgId = null;

/** Error with the HTTP status, so the importer can back off on 429. */
function httpError(message, resp) {
  const err = new Error(`${message}: ${resp.status}`);
  err.status = resp.status;
  const retryAfter = Number(resp.headers?.get?.('retry-after'));
  if (Number.isFinite(retryAfter) && retryAfter > 0) err.retryAfterMs = retryAfter * 1000;
  return err;
}

/**
 * Detect the chat-capable organization ID.
 */
async function getOrgId() {
  if (cachedOrgId) return cachedOrgId;

  const resp = await fetch(CLAUDE_ORG_API, {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  });

  if (!resp.ok) {
    throw httpError(
      resp.status === 401 || resp.status === 403
        ? 'Not signed in to Claude in this tab. Sign in and try again'
        : 'Failed to fetch organizations',
      resp,
    );
  }

  const orgs = await resp.json();
  const chatOrg = orgs.find((org) =>
    org.capabilities && org.capabilities.includes('chat')
  );
  cachedOrgId = chatOrg ? chatOrg.uuid : orgs[0]?.uuid;
  if (!cachedOrgId) throw new Error('No organizations found');
  return cachedOrgId;
}

/**
 * One page of conversations, most recently updated first.
 * cursor: { offset, seen } from the previous page, or undefined to start.
 * Returns { items, next } where next is null after the last page. Stops when a
 * page adds nothing new, so it also ends if the API ignores paging.
 */
async function listPage(cursor = { offset: 0, seen: [] }, limit = 100) {
  const orgId = await getOrgId();
  const url = `${CLAUDE_CONVERSATIONS_API(orgId)}?limit=${limit}&offset=${cursor.offset}`;
  const resp = await fetch(url, {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  });
  if (!resp.ok) throw httpError('Failed to list conversations', resp);
  const page = await resp.json();
  const seen = new Set(cursor.seen);
  const items = [];
  for (const conv of Array.isArray(page) ? page : page.data || []) {
    if (!conv?.uuid || seen.has(conv.uuid)) continue;
    seen.add(conv.uuid);
    // Temporary (incognito) chats are not kept.
    if (!conv.is_temporary) items.push({ ...conv, id: conv.uuid });
  }
  const added = seen.size - cursor.seen.length;
  const next = added < limit ? null : { offset: cursor.offset + limit, seen: [...seen] };
  return { items, next };
}

/** Every conversation (all pages). */
async function listConversations() {
  const all = [];
  for (let cursor; ;) {
    const { items, next } = await listPage(cursor);
    all.push(...items);
    if (!next) return all;
    cursor = next;
  }
}

/**
 * Fetch one full conversation with all messages.
 * Walks the active lineage from current_leaf_message_uuid.
 */
async function fetchConversation(conversationId) {
  const orgId = await getOrgId();
  const resp = await fetch(CLAUDE_CONVERSATION_API(orgId, conversationId), {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  });

  if (!resp.ok) {
    throw httpError(`Failed to fetch conversation ${conversationId}`, resp);
  }

  const raw = await resp.json();

  // Build message index by UUID.
  const msgMap = {};
  for (const msg of raw.chat_messages || []) {
    msgMap[msg.uuid] = msg;
  }

  // Walk active lineage from leaf.
  const activeMessages = [];
  let current = raw.current_leaf_message_uuid;
  while (current && msgMap[current]) {
    activeMessages.unshift(msgMap[current]);
    current = msgMap[current].parent_message_uuid;
  }

  // Convert to normalized format, dropping messages with no text.
  const messages = activeMessages.map((msg) => {
    const role = msg.sender === 'human' ? 'user' : 'assistant';
    // Extract text from content blocks.
    const textBlocks = (msg.content || [])
      .filter((b) => b.type === 'text' && b.text)
      .map((b) => b.text);
    const thinkingBlocks = (msg.content || [])
      .filter((b) => b.type === 'thinking' && b.thinking)
      .map((b) => `[thinking] ${b.thinking}`);
    // Older conversations carry plain `text` instead of content blocks.
    const content = [...thinkingBlocks, ...textBlocks].join('\n\n') || msg.text || '';

    return {
      provider_message_id: msg.uuid,
      role,
      content,
      created_at: msg.created_at,
      model: role === 'assistant' ? raw.model : null,
    };
  }).filter((m) => m.content.trim());

  // Extract attachment references.
  const attachments = [];
  for (const msg of activeMessages) {
    for (const att of msg.attachments || []) {
      attachments.push({
        provider_attachment_id: att.id,
        file_name: att.file_name,
        file_type: att.file_type,
        file_size: att.file_size,
      });
    }
  }

  return {
    provider: 'claude',
    provider_conversation_id: raw.uuid,
    title: raw.name || '',
    model: raw.model,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
    messages,
    attachments,
  };
}

/**
 * Extract the currently open conversation from the page context.
 * Used for save-on-click — reads the conversation ID from the URL,
 * then fetches via the API.
 */
async function extractCurrentConversation() {
  const url = new URL(window.location.href);
  const match = url.pathname.match(/\/chat\/([a-zA-Z0-9-]+)/);
  if (!match) return null;

  const conversationId = match[1];
  return fetchConversation(conversationId);
}

// Registered for the content script: MV3 content scripts are classic scripts
// and cannot import ES modules, so providers attach to a shared global.
(globalThis.__subzeroProviders ||= {}).claude = {
  getOrgId, listPage, listConversations, fetchConversation, extractCurrentConversation,
};

})();
