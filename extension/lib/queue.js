// Subzero — durable capture queue.
//
// Captures wait in chrome.storage.local until the paired server confirms them,
// so nothing is lost if the browser closes or the server is asleep.
//
// - One entry per conversation: a newer capture of the same conversation
//   replaces the queued one (the server deduplicates messages anyway).
// - Every read-modify-write of the queue runs under one lock, so concurrent
//   captures and deliveries never overwrite each other. The network call runs
//   outside the lock, so capturing never waits for the server.
// - One delivery loop at a time. It starts right after a capture and on the
//   periodic alarm, and sends items one by one.
// - Failures back off exponentially. A request the server rejects outright
//   (4xx other than 401/408/429) goes straight to the failed list; anything else
//   moves there after MAX_ATTEMPTS. Failed items are kept and can be retried.
// - A rejected token (401) pauses delivery without spending attempts: the
//   items wait for the browser to be paired again.

import { ingestConversation } from './api.js';

const QUEUE_KEY = 'captureQueue';
const DEAD_LETTER_KEY = 'deadLetterQueue';
const MAX_ATTEMPTS = 8;
const BASE_DELAY_MS = 30 * 1000;
const MAX_DELAY_MS = 60 * 60 * 1000;

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

function backoff(attempts) {
  return Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** Math.max(0, attempts - 1));
}

function isPermanent(status) {
  return status >= 400 && status < 500 && ![401, 408, 429].includes(status);
}

async function readQueue() {
  const { [QUEUE_KEY]: queue } = await chrome.storage.local.get(QUEUE_KEY);
  return queue || [];
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
    const items = await readQueue();
    const entry = {
      ...payload,
      _key: itemKey(payload),
      _queuedAt: Date.now(),
      _attempts: 0,
      _nextAttemptAt: 0,
    };
    const index = items.findIndex((item) => item._key === entry._key);
    if (index >= 0) items[index] = entry;
    else items.push(entry);
    await chrome.storage.local.set({ [QUEUE_KEY]: items });
    return { queued: items.length };
  });
}

/** The first item due now, or null. */
function nextDue(items, now) {
  return items.find((item) => (item._nextAttemptAt || 0) <= now) || null;
}

async function recordSuccess(item, result) {
  await locked(async () => {
    const items = await readQueue();
    // Remove the item only if it was not replaced by a newer capture meanwhile.
    const remaining = items.filter(
      (other) => !(other._key === item._key && other._queuedAt === item._queuedAt),
    );
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
  });
}

async function recordFailure(item, err) {
  await locked(async () => {
    const items = await readQueue();
    const index = items.findIndex(
      (other) => other._key === item._key && other._queuedAt === item._queuedAt,
    );
    const { deliveryErrors = {} } = await chrome.storage.local.get('deliveryErrors');
    deliveryErrors[item.provider] = err.message;
    if (index < 0) {
      // Replaced by a newer capture while sending: that one gets its own try.
      await chrome.storage.local.set({ deliveryErrors });
      return;
    }
    const current = items[index];
    current._attempts = (current._attempts || 0) + 1;
    current._lastError = err.message;
    current._nextAttemptAt = Date.now() + backoff(current._attempts);
    if (isPermanent(err.status) || current._attempts >= MAX_ATTEMPTS) {
      items.splice(index, 1);
      const { [DEAD_LETTER_KEY]: dead = [] } = await chrome.storage.local.get(DEAD_LETTER_KEY);
      const others = dead.filter((d) => d._key !== current._key);
      others.push({ ...current, _failedAt: Date.now() });
      await chrome.storage.local.set({
        [QUEUE_KEY]: items, [DEAD_LETTER_KEY]: others, deliveryErrors,
      });
    } else {
      await chrome.storage.local.set({ [QUEUE_KEY]: items, deliveryErrors });
    }
  });
}

/**
 * Deliver one due item. Returns 'sent', 'failed', 'blocked' (token rejected:
 * stop until re-paired), or 'idle' (nothing due).
 */
async function processOne() {
  const item = await locked(async () => nextDue(await readQueue(), Date.now()));
  if (!item) return 'idle';
  try {
    const result = await ingestConversation(item);
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
  }
}

let draining = null;
/**
 * Deliver everything that is due, one item at a time. Concurrent callers share
 * the same run. Stops when nothing is due, on a rejected token, or when the
 * server is unreachable (the next alarm tries again).
 */
function drain() {
  if (!draining) {
    draining = (async () => {
      const { isPaused } = await chrome.storage.local.get('isPaused');
      if (isPaused) return;
      for (;;) {
        const outcome = await processOne();
        if (outcome !== 'sent') return;
      }
    })().finally(() => { draining = null; });
  }
  return draining;
}

/** Move every failed item back into the queue, due now. */
function retryFailed() {
  return locked(async () => {
    const { [DEAD_LETTER_KEY]: dead = [] } = await chrome.storage.local.get(DEAD_LETTER_KEY);
    if (!dead.length) return 0;
    const items = await readQueue();
    for (const failed of dead) {
      const { _failedAt, ...item } = failed;
      if (items.some((other) => other._key === item._key)) continue;
      items.push({ ...item, _attempts: 0, _nextAttemptAt: 0 });
    }
    await chrome.storage.local.set({ [QUEUE_KEY]: items, [DEAD_LETTER_KEY]: [] });
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
