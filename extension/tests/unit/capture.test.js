const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json')));
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const moduleUrl = (file) => pathToFileURL(path.join(root, file)).href;

function contentContext(providerModule, sendMessage = async () => ({ ok: true })) {
  let listener;
  const context = vm.createContext({
    URL, console, setTimeout, clearTimeout,
    window: { location: { hostname: 'chatgpt.com', href: 'https://chatgpt.com/c/test' } },
    document: { body: {}, querySelector: () => null },
    MutationObserver: class { observe() {} },
    chrome: { runtime: { sendMessage, onMessage: { addListener(fn) { listener = fn; } } } },
    __subzeroProviders: { chatgpt: providerModule },
  });
  vm.runInContext(read('content.js'), context);
  return (request) => new Promise((resolve) => listener(request, {}, resolve));
}

const payload = { provider: 'chatgpt', provider_conversation_id: 'test', messages: [{ content: 'hello' }] };

test('all manifest providers retain their own helpers and tolerate reinjection', async () => {
  const calls = [];
  const context = vm.createContext({
    URL, console,
    window: { location: { href: 'https://claude.ai/chat/claude-test' } },
    fetch: async (url) => {
      calls.push(url);
      if (url.endsWith('/organizations')) return { ok: true, json: async () => [{ uuid: 'org', capabilities: ['chat'] }] };
      if (url.includes('claude.ai')) return { ok: true, json: async () => ({ uuid: 'claude-test', chat_messages: [] }) };
      if (url.endsWith('/api/auth/session')) return { ok: true, json: async () => ({ accessToken: 'test-token' }) };
      return { ok: true, json: async () => ({ conversation_id: 'gpt-test', mapping: {} }) };
    },
  });
  const files = manifest.content_scripts[0].js.filter((file) => file.startsWith('providers/'));
  for (let pass = 0; pass < 2; pass++) for (const file of files) vm.runInContext(read(file), context);
  assert.equal(Object.keys(context.__subzeroProviders).length, 5);
  assert.equal((await context.__subzeroProviders.claude.extractCurrentConversation()).provider, 'claude');
  context.window.location.href = 'https://chatgpt.com/c/gpt-test';
  assert.equal((await context.__subzeroProviders.chatgpt.extractCurrentConversation()).provider, 'chatgpt');
  assert(calls.some((url) => url.includes('/organizations/org/chat_conversations/claude-test')));
  assert(calls.some((url) => url.includes('/backend-api/conversation/gpt-test')));
});

test('backfill returns listing failures to the popup', async () => {
  const send = contentContext({ fetchConversation() {}, listConversations() { throw Error('Session expired'); } });
  const response = await send({ action: 'backfill', dryRun: false });
  assert.equal(response.ok, false);
  assert.equal(response.error, 'Session expired');
});

test('capture reports a failed queue acknowledgement', async () => {
  const send = contentContext({ extractCurrentConversation: async () => payload }, async () => ({ ok: false, error: 'Storage full' }));
  const response = await send({ action: 'capture' });
  assert.equal(response.ok, false);
  assert.equal(response.error, 'Storage full');
});

test('DOM-only backfill queues the open conversation without requiring a sidebar list', async () => {
  let queued = 0;
  const send = contentContext({ extractCurrentConversation: async () => payload }, async () => { queued++; return { ok: true }; });
  const result = await send({ action: 'backfill', dryRun: false });
  assert.equal(result.scope, 'current');
  assert.equal(result.captured, 1);
  assert.equal(queued, 1);
});

test('missing conversation IDs are surfaced before enqueue', async () => {
  const send = contentContext({ extractCurrentConversation: async () => ({ ...payload, provider_conversation_id: '' }) }, async () => assert.fail('must not queue'));
  assert.match((await send({ action: 'capture' })).error, /No conversation ID/);
});

test('missing receivers recover through ordered isolated script injection', async () => {
  const { sendToProviderTab } = await import(moduleUrl('lib/tabs.js'));
  let ready = false;
  const operations = [];
  global.chrome = {
    runtime: { getManifest: () => manifest },
    tabs: {
      get: async () => ({ url: 'https://chatgpt.com/c/test' }),
      sendMessage: async (_id, message) => {
        if (!ready) throw Error('Receiving end does not exist');
        operations.push(message.action);
        return { ok: true };
      },
    },
    scripting: { executeScript: async (options) => {
      assert.equal(options.world, 'ISOLATED');
      assert.deepEqual(options.files, manifest.content_scripts[0].js);
      ready = true;
    } },
  };
  assert(manifest.permissions.includes('scripting'));
  await sendToProviderTab(1, { action: 'backfill' });
  assert.deepEqual(operations, ['ping', 'backfill']);
  chrome.tabs.get = async () => ({ url: 'https://chatgpt.com.evil.example/' });
  await assert.rejects(sendToProviderTab(1, { action: 'capture' }), /supported chat/);
});

test('queue preserves concurrent captures and confirms only successful delivery', async () => {
  const state = { deviceToken: 'test-token', serverUrl: 'https://server.example', lastSync: { chatgpt: 'legacy-capture-time' } };
  global.chrome = { storage: { local: {
    get: async (keys) => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).map((key) => [key, structuredClone(state[key])])),
    set: async (values) => Object.assign(state, structuredClone(values)),
  } } };
  const queue = await import(moduleUrl('lib/queue.js'));
  let release;
  let started;
  const waiting = new Promise((resolve) => { started = resolve; });
  global.fetch = async () => {
    started();
    await new Promise((resolve) => { release = resolve; });
    return { ok: true, url: 'https://server.example/api/v1/ingest/conversation', json: async () => ({ conversation_id: 'db-id', messages_inserted: 1, status: 'created' }) };
  };
  await queue.enqueue(payload);
  assert.equal(Object.keys((await queue.getStatus()).lastDelivery).length, 0);
  const processing = queue.processOne();
  await waiting;
  const adding = queue.enqueue({ ...payload, provider_conversation_id: 'second' });
  release();
  await Promise.all([processing, adding]);
  assert.equal(state.captureQueue.length, 1);
  assert.equal(state.captureQueue[0].provider_conversation_id, 'second');
  assert.equal(state.lastDelivery.chatgpt.conversationId, 'db-id');
  assert.equal(state.lastDelivery.chatgpt.serverUrl, 'https://server.example');
  const delivery = structuredClone(state.lastDelivery);
  global.fetch = async () => ({ ok: false, status: 503, text: async () => 'Server unavailable' });
  await Promise.all([queue.processOne(), queue.enqueue({ ...payload, provider_conversation_id: 'third' })]);
  assert.deepEqual(state.lastDelivery, delivery);
  assert.match(state.deliveryErrors.chatgpt, /503/);
  assert.equal(state.captureQueue.length, 2);
});
