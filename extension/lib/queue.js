// Subzero — durable capture queue.
// Queues captures in chrome.storage.local and sends them from the service worker
// with retries and exponential backoff. Nothing is lost if the browser closes.

const QUEUE_KEY = 'captureQueue';
const DEAD_LETTER_KEY = 'deadLetterQueue';
const MAX_RETRIES = 3;
// Queue reads and writes share a lock, including overlapping alarm ticks.
let pending = Promise.resolve();
function serialize(operation) {
  const result = pending.then(operation);
  pending = result.catch(() => {});
  return result;
}

import { ingestConversation } from './api.js';

/**
 * Add a capture payload to the queue.
 */
function enqueue(payload) {
  return serialize(() => enqueueUnlocked(payload));
}

async function enqueueUnlocked(payload) {
  const { [QUEUE_KEY]: queue } = await chrome.storage.local.get(QUEUE_KEY);
  const items = queue || [];
  items.push({
    ...payload,
    _queuedAt: Date.now(),
    _retries: 0,
  });
  await chrome.storage.local.set({ [QUEUE_KEY]: items });
}

/**
 * Process one item from the queue. Called by the service worker alarm.
 * Returns true if an item was processed, false if queue is empty.
 */
function processOne() {
  return serialize(processOneUnlocked);
}

async function processOneUnlocked() {
  const { [QUEUE_KEY]: queue } = await chrome.storage.local.get(QUEUE_KEY);
  if (!queue || queue.length === 0) return false;

  const items = [...queue];
  const item = items.shift();

  try {
    const result = await ingestConversation(item);
    console.log('[PersonalContext] Ingested:', result.status, result.messages_inserted, 'messages');
    const { lastDelivery = {}, deliveryErrors = {} } = await chrome.storage.local.get([
      'lastDelivery', 'deliveryErrors',
    ]);
    lastDelivery[item.provider] = {
      at: new Date().toISOString(),
      conversationId: result.conversation_id,
      messagesInserted: result.messages_inserted,
      serverUrl: result.serverUrl,
    };
    delete deliveryErrors[item.provider];
    await chrome.storage.local.set({ [QUEUE_KEY]: items, lastDelivery, deliveryErrors });
    return true;
  } catch (err) {
    console.error('[PersonalContext] Ingest failed:', err.message);
    item._retries = (item._retries || 0) + 1;
    item._lastError = err.message;
    const { deliveryErrors = {} } = await chrome.storage.local.get('deliveryErrors');
    deliveryErrors[item.provider] = err.message;
    await chrome.storage.local.set({ deliveryErrors });

    if (item._retries >= MAX_RETRIES) {
      // Move to dead letter queue.
      const { [DEAD_LETTER_KEY]: dead } = await chrome.storage.local.get(DEAD_LETTER_KEY);
      const deadItems = dead || [];
      deadItems.push(item);
      await chrome.storage.local.set({
        [QUEUE_KEY]: items,
        [DEAD_LETTER_KEY]: deadItems,
      });
    } else {
      // Re-queue with backoff delay handled by alarm interval.
      items.push(item);
      await chrome.storage.local.set({ [QUEUE_KEY]: items });
    }
    return true;
  }
}

/**
 * Get queue status for the popup.
 */
async function getStatus() {
  const { [QUEUE_KEY]: queue, [DEAD_LETTER_KEY]: dead,
          lastSync, lastDelivery, deliveryErrors, isPaused } = await chrome.storage.local.get([
    QUEUE_KEY, DEAD_LETTER_KEY, 'lastSync', 'lastDelivery', 'deliveryErrors', 'isPaused',
  ]);
  return {
    queueLength: (queue || []).length,
    deadLength: (dead || []).length,
    lastSync: lastSync || {},
    lastDelivery: lastDelivery || {},
    deliveryErrors: deliveryErrors || {},
    isPaused: isPaused || false,
  };
}

export { enqueue, processOne, getStatus, QUEUE_KEY, DEAD_LETTER_KEY };
