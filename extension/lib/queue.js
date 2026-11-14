// Subzero — durable capture queue.
//
// Captures wait in chrome.storage.local until the paired server confirms them,
// so nothing is lost if the browser closes or the server is asleep.
//
// Storage layout, so a large import stays cheap:
//   captureQueue      the line: one small header per conversation, in order
//   queue:<key>       that conversation's payload (its messages), one entry each
//   deadLetterQueue   items the server refused or that ran out of attempts
// Adding, sending or retrying one conversation reads and writes only the
// index and that conversation's own entry, never every queued message.
//
// - One entry per conversation: a newer capture of the same conversation
//   replaces the queued one (the server deduplicates messages anyway).
// - Every read-modify-write of the queue runs under one lock, so concurrent
//   captures and deliveries never overwrite each other. Network calls run
//   outside the lock, so capturing never waits for the server.
// - Up to DELIVERY_CONCURRENCY conversations are sent at once (the server
//   handles them in parallel), never the same conversation twice at once.
// - Failures back off exponentially. A request the server rejects outright
//   (4xx other than 401/408/429) goes straight to the failed list; anything else
//   moves there after MAX_ATTEMPTS. Failed items are kept and can be retried.
// - A rejected token (401) pauses delivery without spending attempts: the
//   items wait for the browser to be paired again.

import { ingestConversation } from './api.js';

const QUEUE_KEY = 'captureQueue';
const DEAD_LETTER_KEY = 'deadLetterQueue';
const BODY_PREFIX = 'queue:';
const MAX_ATTEMPTS = 8;
const BASE_DELAY_MS = 30 * 1000;
const MAX_DELAY_MS = 60 * 60 * 1000;
const DELIVERY_CONCURRENCY = 3;

let lock = Promise.resolve();
/** Run fn after every earlier queue operation, one at a time. */
function locked(fn) {
  const result = lock.then(fn);
  lock = result.catch(() => {});
  return result;
}

function itemKey(item) {
  return `${item.provider}:${item.provider_conversation_id}`;
}

const bodyKey = (key) => `${BODY_PREFIX}${key}`;

function backoff(attempts) {
  return Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** Math.max(0, attempts - 1));
}

function isPermanent(status) {
  return status >= 400 && status < 500 && ![401, 408, 429].includes(status);
}

/** The header kept in the index: everything but the messages. */
function headerOf(entry) {
  const { messages, attachments, ...header } = entry;
  return { ...header, _messages: (messages || []).length };
}

/**
 * The index. Queues written before 0.2.1 kept whole payloads in it; those
 * are split into per-conversation entries the first time they are read.
 */
async function readQueue() {
  const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
  const legacy = queue.filter((item) => Array.isArray(item.messages));
  if (!legacy.length) return queue;
  const updates = {};
  for (const item of legacy) updates[bodyKey(item._key || itemKey(item))] = item;
  const index = queue.map((item) => (Array.isArray(item.messages)
    ? headerOf({ ...item, _key: item._key || itemKey(item) }) : item));
  await chrome.storage.local.set({ ...updates, [QUEUE_KEY]: index });
  return index;
}

async function readBody(key) {
  const name = bodyKey(key);
  const { [name]: body } = await chrome.storage.local.get(name);
  return body || null;
}

/**
 * Add a capture. Replaces a queued capture of the same conversation, keeping
 * its place in line. Resolves once the capture is persisted.
 */
function enqueue(payload) {
  if (!payload?.provider || !payload?.provider_conversation_id) {
    return Promise.reject(new Error('Capture is missing its provider or conversation ID'));
  }
  return locked(async () => {
    const index = await readQueue();
    const entry = {
      ...payload,
      _key: itemKey(payload),
      _queuedAt: Date.now(),
      _attempts: 0,
      _nextAttemptAt: 0,
    };
    const at = index.findIndex((item) => item._key === entry._key);
    if (at >= 0) {
      const older = await readBody(entry._key);
      entry.messages = mergeMessages(older?.messages, entry.messages);
      if (!String(entry.title || '').trim()) entry.title = index[at].title;
      index[at] = headerOf(entry);
    } else {
      index.push(headerOf(entry));
    }
    await chrome.storage.local.set({ [QUEUE_KEY]: index, [bodyKey(entry._key)]: entry });
    return { queued: index.length };
  });
}

/**
 * Messages of a newer capture, plus any the older unsent capture had that the
 * newer one is missing (a page read before it finished scrolling, say). The
 * newer capture is the usual superset and then comes back unchanged.
 */
function mergeMessages(older = [], newer = []) {
  const id = (m) => m.provider_message_id;
  const newerIds = new Set(newer.map(id).filter(Boolean));
  const missing = older.filter((m) => id(m) && !newerIds.has(id(m)));
  return missing.length ? [...missing, ...newer] : newer;
}

const inFlight = new Set();

/** Claim the first item due now that is not already being sent, with its payload. */
function claimNext() {
  return locked(async () => {
    const now = Date.now();
    let index = await readQueue();
    for (;;) {
      const header = index.find(
        (item) => (item._nextAttemptAt || 0) <= now && !inFlight.has(item._key),
      );
      if (!header) return null;
      const body = await readBody(header._key);
      if (body) {
        inFlight.add(header._key);
        return { ...body, ...header, messages: body.messages || [] };
      }
      // A header whose payload is gone (storage cleared by hand): drop it.
      index = index.filter((item) => item !== header);
      await chrome.storage.local.set({ [QUEUE_KEY]: index });
    }
  });
}

/** Still the same capture (not replaced by a newer one while it was sent)? */
const sameCapture = (a, b) => a._key === b._key && a._queuedAt === b._queuedAt;

async function recordSuccess(item, result) {
  await locked(async () => {
    const index = await readQueue();
    const current = index.find((other) => other._key === item._key);
    const replaced = current && !sameCapture(current, item);
    const remaining = index.filter((other) => !sameCapture(other, item));
    const { lastDelivery = {}, deliveryErrors = {}, delivered = {} } =
      await chrome.storage.local.get(['lastDelivery', 'deliveryErrors', 'delivered']);
    lastDelivery[item.provider] = {
      at: new Date().toISOString(),
      conversationId: result.conversation_id,
      providerConversationId: item.provider_conversation_id,
      title: item.title || '',
      messages: (item.messages || []).length,
      messagesInserted: result.messages_inserted,
      serverUrl: result.serverUrl,
    };
    delete deliveryErrors[item.provider];
    // When the confirmed version was last updated (epoch ms), so imports can
    // skip conversations that have not changed since.
    const updated = Date.parse(item.updated_at || '');
    if (Number.isFinite(updated)) delivered[item._key] = updated;
    else delete delivered[item._key];
    await chrome.storage.local.set({
      [QUEUE_KEY]: remaining, lastDelivery, deliveryErrors, delivered,
    });
    // A newer capture that replaced this one keeps its own payload.
    if (!replaced) await chrome.storage.local.remove(bodyKey(item._key));
  });
}

async function recordFailure(item, err) {
  await locked(async () => {
    const index = await readQueue();
    const at = index.findIndex((other) => sameCapture(other, item));
    const { deliveryErrors = {} } = await chrome.storage.local.get('deliveryErrors');
    deliveryErrors[item.provider] = err.message;
    if (at < 0) {
      // Replaced by a newer capture while sending: that one gets its own try.
      await chrome.storage.local.set({ deliveryErrors });
      return;
    }
    const current = index[at];
    current._attempts = (current._attempts || 0) + 1;
    current._lastError = err.message;
    current._nextAttemptAt = Date.now() + backoff(current._attempts);
    if (isPermanent(err.status) || current._attempts >= MAX_ATTEMPTS) {
      index.splice(at, 1);
      const { [DEAD_LETTER_KEY]: dead = [] } = await chrome.storage.local.get(DEAD_LETTER_KEY);
      const others = dead.filter((d) => d._key !== current._key);
      others.push({ ...item, ...current, _failedAt: Date.now() });
      await chrome.storage.local.set({
        [QUEUE_KEY]: index, [DEAD_LETTER_KEY]: others, deliveryErrors,
      });
      await chrome.storage.local.remove(bodyKey(current._key));
    } else {
      await chrome.storage.local.set({ [QUEUE_KEY]: index, deliveryErrors });
    }
  });
}

/**
 * Deliver one due item. Returns 'sent', 'failed', 'blocked' (token rejected:
 * stop until re-paired), or 'idle' (nothing due).
 */
async function processOne() {
  const item = await claimNext();
  if (!item) return 'idle';
  try {
    const { _key, _queuedAt, _attempts, _nextAttemptAt, _lastError, _messages, ...payload } = item;
    const result = await ingestConversation(payload);
    console.log('[subzero] Delivered', item._key, result.status,
      `${result.messages_inserted} new message(s)`, 'to', result.serverUrl);
    await recordSuccess(item, result);
    return 'sent';
  } catch (err) {
    console.error('[subzero] Delivery failed:', item._key, err.message);
    if (err.status === 401) {
      // Not this item's fault; keep it, untouched, until the browser is re-paired.
      await locked(async () => {
        const { deliveryErrors = {} } = await chrome.storage.local.get('deliveryErrors');
        deliveryErrors[item.provider] = err.message;
        await chrome.storage.local.set({ deliveryErrors });
      });
      return 'blocked';
    }
    await recordFailure(item, err);
    return 'failed';
  } finally {
    inFlight.delete(item._key);
  }
}

let draining = null;
/**
 * Deliver everything that is due, a few conversations at a time. Concurrent
 * callers share the same run. A worker stops when nothing is due, on a
 * rejected token, or when the server is unreachable (the next alarm tries
 * again); a rejected token stops them all.
 */
function drain() {
  if (!draining) {
    draining = (async () => {
      const { isPaused } = await chrome.storage.local.get('isPaused');
      if (isPaused) return;
      let blocked = false;
      const worker = async () => {
        while (!blocked) {
          const outcome = await processOne();
          if (outcome === 'blocked') blocked = true;
          if (outcome !== 'sent') return;
        }
      };
      await Promise.all(Array.from({ length: DELIVERY_CONCURRENCY }, worker));
    })().finally(() => { draining = null; });
  }
  return draining;
}

/** Move every failed item back into the queue, due now. */
function retryFailed() {
  return locked(async () => {
    const { [DEAD_LETTER_KEY]: dead = [] } = await chrome.storage.local.get(DEAD_LETTER_KEY);
    if (!dead.length) return 0;
    const index = await readQueue();
    const bodies = {};
    for (const failed of dead) {
      const { _failedAt, ...item } = failed;
      if (index.some((other) => other._key === item._key)) continue;
      const entry = { ...item, _attempts: 0, _nextAttemptAt: 0 };
      index.push(headerOf(entry));
      bodies[bodyKey(entry._key)] = entry;
    }
    await chrome.storage.local.set({ ...bodies, [QUEUE_KEY]: index, [DEAD_LETTER_KEY]: [] });
    return dead.length;
  });
}

/** Queue status for the popup. */
async function getStatus() {
  const {
    [QUEUE_KEY]: queue = [], [DEAD_LETTER_KEY]: dead = [],
    lastDelivery = {}, deliveryErrors = {}, isPaused = false, serverUrl = null,
  } = await chrome.storage.local.get([
    QUEUE_KEY, DEAD_LETTER_KEY, 'lastDelivery', 'deliveryErrors', 'isPaused', 'serverUrl',
  ]);
  return {
    queueLength: queue.length,
    deadLength: dead.length,
    lastFailure: dead.length ? dead[dead.length - 1]._lastError : null,
    lastDelivery,
    deliveryErrors,
    isPaused,
    serverUrl,
  };
}

export {
  enqueue, processOne, drain, retryFailed, getStatus, QUEUE_KEY, DEAD_LETTER_KEY, MAX_ATTEMPTS,
};
