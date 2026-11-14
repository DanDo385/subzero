// Regression tests for capture, delivery and pairing. Run: node --test tests/unit/*.test.js
const { test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json')));
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const moduleUrl = (file) => pathToFileURL(path.join(root, file)).href;
const tick = () => new Promise((r) => setImmediate(r));
// Values made inside a vm context come from another realm; compare them as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));

/** chrome.storage.local backed by a plain object, with structured cloning like the real one. */
function fakeStorage(state) {
  return {
    get: async (keys) => {
      const list = keys == null ? Object.keys(state) : Array.isArray(keys) ? keys : [keys];
      return Object.fromEntries(list.filter((k) => k in state).map((k) => [k, structuredClone(state[k])]));
    },
    set: async (values) => { Object.assign(state, structuredClone(values)); },
    remove: async (keys) => { for (const k of [].concat(keys)) delete state[k]; },
  };
}

/** Run content.js in a fake provider page. Returns a function that sends it a message. */
function contentPage({ host = 'chatgpt.com', providers = {}, sendMessage = async () => ({ ok: true }), state = {} } = {}) {
  const listeners = [];
  const context = vm.createContext({
    URL, console, setTimeout, clearTimeout, Date, Math,
    window: { location: { hostname: host, href: `https://${host}/c/test` } },
    document: { body: {}, querySelector: () => null },
    MutationObserver: class { observe() {} disconnect() {} },
    chrome: {
      runtime: { sendMessage, onMessage: { addListener(fn) { listeners.push(fn); } } },
      storage: { local: fakeStorage(state) },
    },
    __subzeroProviders: providers,
  });
  context.window.__subzeroInstance = undefined;
  // content.js reads window.__subzeroInstance; in a page, window === globalThis.
  context.window = context;
  context.location = { hostname: host, href: `https://${host}/c/test` };
  const load = () => vm.runInContext(read('content.js'), context);
  load();
  const send = (request) => new Promise((resolve) => {
    let answered = false;
    for (const fn of listeners) {
      const async = fn(request, {}, (r) => { answered = true; resolve(r); });
      if (async === true || answered) return;
    }
    resolve(undefined);
  });
  return { send, load, listeners, state };
}

const payload = {
  provider: 'chatgpt', provider_conversation_id: 'conv-1', title: 'Test',
  updated_at: '2026-11-13T10:00:00.000Z', messages: [{ provider_message_id: 'm1', role: 'user', content: 'hello' }],
};

// ---------------------------------------------------------------- providers

test('every provider registers its own helpers, also when injected twice', async () => {
  const calls = [];
  const json = (body) => ({ ok: true, status: 200, json: async () => body });
  const context = vm.createContext({
    URL, console,
    window: { location: { href: 'https://claude.ai/chat/claude-test' } },
    fetch: async (url) => {
      calls.push(url);
      if (url.endsWith('/organizations')) return json([{ uuid: 'org', capabilities: ['chat'] }]);
      if (url.includes('claude.ai')) {
        return json({
          uuid: 'claude-test', current_leaf_message_uuid: 'b',
          chat_messages: [
            { uuid: 'a', sender: 'human', content: [{ type: 'text', text: 'question' }] },
            { uuid: 'b', parent_message_uuid: 'a', sender: 'assistant', content: [{ type: 'text', text: 'answer' }] },
            { uuid: 'x', sender: 'assistant', content: [{ type: 'text', text: 'abandoned branch' }] },
          ],
        });
      }
      if (url.endsWith('/api/auth/session')) return json({ accessToken: 'test-token' });
      return json({
        conversation_id: 'gpt-test', current_node: 'n3', mapping: {
          n1: { message: { id: 'n1', author: { role: 'system' }, content: { parts: [''] } } },
          n2: { parent: 'n1', message: { id: 'n2', author: { role: 'user' }, content: { content_type: 'text', parts: ['hi'] } } },
          n3: { parent: 'n2', message: { id: 'n3', author: { role: 'assistant' }, content: { content_type: 'code', text: 'print(1)' } } },
        },
      });
    },
  });
  const files = manifest.content_scripts[0].js.filter((file) => file.startsWith('providers/'));
  for (let pass = 0; pass < 2; pass++) for (const file of files) vm.runInContext(read(file), context);
  assert.deepEqual(plain(Object.keys(context.__subzeroProviders).sort()), ['chatgpt', 'claude', 'gemini', 'grok', 'muse']);

  const claude = await context.__subzeroProviders.claude.extractCurrentConversation();
  assert.equal(claude.provider, 'claude');
  assert.deepEqual(plain(claude.messages.map((m) => m.content)), ['question', 'answer']);

  context.window.location.href = 'https://chatgpt.com/c/gpt-test';
  const gpt = await context.__subzeroProviders.chatgpt.extractCurrentConversation();
  assert.equal(gpt.provider, 'chatgpt');
  // The empty system root is dropped; a code message keeps its text.
  assert.deepEqual(plain(gpt.messages.map((m) => [m.role, m.content])), [['user', 'hi'], ['assistant', 'print(1)']]);
  assert(calls.some((url) => url.includes('/organizations/org/chat_conversations/claude-test')));
});

test('chatgpt refreshes an expired access token once', async () => {
  let sessions = 0;
  const context = vm.createContext({
    URL, console,
    window: { location: { href: 'https://chatgpt.com/c/abc' } },
    fetch: async (url, init) => {
      if (url.endsWith('/api/auth/session')) {
        sessions++;
        return { ok: true, status: 200, json: async () => ({ accessToken: `t${sessions}` }) };
      }
      if (init.headers.Authorization === 'Bearer t1') return { ok: false, status: 401, headers: new Map() };
      return { ok: true, status: 200, json: async () => ({ conversation_id: 'abc', mapping: {}, current_node: null }) };
    },
  });
  vm.runInContext(read('providers/chatgpt.js'), context);
  const data = await context.__subzeroProviders.chatgpt.extractCurrentConversation();
  assert.equal(data.provider_conversation_id, 'abc');
  assert.equal(sessions, 2);
});

test('claude history listing pages until no new conversations arrive', async () => {
  const all = Array.from({ length: 150 }, (_, i) => ({ uuid: `c${i}` }));
  const context = vm.createContext({
    URL, console,
    fetch: async (url) => {
      if (url.endsWith('/organizations')) return { ok: true, json: async () => [{ uuid: 'org', capabilities: ['chat'] }] };
      const offset = Number(new URL(url).searchParams.get('offset'));
      return { ok: true, json: async () => all.slice(offset, offset + 100) };
    },
  });
  vm.runInContext(read('providers/claude.js'), context);
  assert.equal((await context.__subzeroProviders.claude.listConversations()).length, 150);

  // An API that ignores paging and always returns everything still terminates.
  context.fetch = async (url) => (url.endsWith('/organizations')
    ? { ok: true, json: async () => [{ uuid: 'org', capabilities: ['chat'] }] }
    : { ok: true, json: async () => all });
  vm.runInContext(read('providers/claude.js'), context);
  assert.equal((await context.__subzeroProviders.claude.listConversations()).length, 150);
});

// ---------------------------------------------------------------- content script

test('capture waits for the queue and reports a failed acknowledgement', async () => {
  const page = contentPage({
    providers: { chatgpt: { extractCurrentConversation: async () => payload } },
    sendMessage: async () => ({ ok: false, error: 'Storage full' }),
  });
  const response = await page.send({ action: 'capture' });
  assert.equal(response.ok, false);
  assert.equal(response.error, 'Storage full');
});

test('missing conversation IDs are refused before queueing', async () => {
  const page = contentPage({
    providers: { chatgpt: { extractCurrentConversation: async () => ({ ...payload, provider_conversation_id: '' }) } },
    sendMessage: async () => assert.fail('must not queue'),
  });
  assert.match((await page.send({ action: 'capture' })).error, /No conversation ID/);
});

test('a re-injected content script takes over from the orphaned copy', async () => {
  let queued = 0;
  const page = contentPage({
    providers: { chatgpt: { extractCurrentConversation: async () => payload } },
    sendMessage: async () => { queued++; return { ok: true }; },
  });
  page.load(); // second injection, as after an extension reload
  assert.equal(page.listeners.length, 2);
  const response = await page.send({ action: 'capture' });
  assert.equal(response.ok, true);
  assert.equal(queued, 1, 'only the current copy may handle a message');
  assert.equal(page.listeners[0]({ action: 'ping' }, {}, () => assert.fail('stale copy answered')), false);
});

test('history import reports listing failures to the popup', async () => {
  const page = contentPage({
    providers: { chatgpt: { fetchConversation() {}, listConversations() { throw Error('Session expired'); } } },
  });
  const response = await page.send({ action: 'backfill' });
  assert.equal(response.ok, false);
  assert.equal(response.error, 'Session expired');
});

test('DOM-only providers import the open conversation and say so', async () => {
  let queued = 0;
  const page = contentPage({
    host: 'grok.com',
    providers: { grok: { extractCurrentConversation: async () => ({ ...payload, provider: 'grok' }), listConversations: async () => [] } },
    sendMessage: async () => { queued++; return { ok: true }; },
  });
  const result = await page.send({ action: 'backfill' });
  assert.equal(result.scope, 'current');
  assert.equal(result.captured, 1);
  assert.equal(queued, 1);
});

test('history import skips conversations the server already has and records progress', async () => {
  const fetched = [];
  const queuedIds = [];
  const state = {
    // conv-old was confirmed at its current version; conv-new was confirmed at an older one.
    delivered: {
      'chatgpt:conv-old': Date.parse('2026-11-01T00:00:00Z'),
      'chatgpt:conv-new': Date.parse('2026-10-01T00:00:00Z'),
    },
  };
  const convs = [
    { id: 'conv-old', update_time: '2026-11-01T00:00:00.000000+00:00' },
    { id: 'conv-new', update_time: '2026-11-12T00:00:00.000000+00:00' },
    { id: 'conv-unseen', update_time: '2026-11-12T00:00:00Z' },
    { id: 'conv-broken', update_time: '2026-11-12T00:00:00Z' },
  ];
  const page = contentPage({
    state,
    providers: { chatgpt: {
      listConversations: async () => convs,
      fetchConversation: async (id) => {
        fetched.push(id);
        if (id === 'conv-broken') throw Object.assign(Error('Failed: 500'), { status: 500 });
        return { ...payload, provider_conversation_id: id };
      },
    } },
    sendMessage: async (m) => { queuedIds.push(m.payload.provider_conversation_id); return { ok: true }; },
  });
  const started = await page.send({ action: 'backfill' });
  assert.equal(started.ok, true);
  assert.equal(started.total, 4);
  assert.equal(started.toFetch, 3);

  for (let i = 0; i < 200 && state['backfill:chatgpt']?.state !== 'done'; i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
  const progress = state['backfill:chatgpt'];
  assert.equal(progress.state, 'done');
  assert.deepEqual(fetched, ['conv-new', 'conv-unseen', 'conv-broken']);
  assert.deepEqual(queuedIds, ['conv-new', 'conv-unseen']);
  assert.equal(progress.queued, 2);
  assert.equal(progress.failed, 1);
  assert.equal(progress.unchanged, 1);
  assert.match(progress.lastError, /500/);
});

// ---------------------------------------------------------------- tab recovery

test('a tab without a live listener gets the packaged scripts injected', async () => {
  const { sendToProviderTab } = await import(moduleUrl('lib/tabs.js'));
  let ready = false;
  const operations = [];
  global.chrome = {
    runtime: { getManifest: () => manifest },
    tabs: {
      get: async () => ({ url: 'https://chatgpt.com/c/test' }),
      sendMessage: async (_id, message) => {
        if (!ready) throw Error('Could not establish connection. Receiving end does not exist.');
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
  await sendToProviderTab(1, { action: 'backfill' });
  assert.deepEqual(operations, ['ping', 'backfill']);
  chrome.tabs.get = async () => ({ url: 'https://chatgpt.com.evil.example/' });
  await assert.rejects(sendToProviderTab(1, { action: 'capture' }), /supported chat page/);
});

// ---------------------------------------------------------------- queue and delivery

let state;
let requests;
let respond;
beforeEach(() => {
  state = { deviceToken: 'pcd_test', serverUrl: 'https://server.example' };
  requests = [];
  respond = async () => ({
    ok: true, status: 200, url: 'https://server.example/api/v1/ingest/conversation',
    json: async () => ({ conversation_id: 'db-1', status: 'created', messages_inserted: 1, messages_skipped: 0 }),
  });
  global.chrome = { storage: { local: fakeStorage(state) } };
  global.fetch = async (url, init) => {
    requests.push({ url, body: init.body ? JSON.parse(init.body) : null });
    return respond(url, init);
  };
});

test('a newer capture of the same conversation replaces the queued one', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  await queue.enqueue(payload);
  await queue.enqueue({ ...payload, provider_conversation_id: 'conv-2' });
  await queue.enqueue({ ...payload, messages: [...payload.messages, { provider_message_id: 'm2', content: 'more' }] });
  assert.deepEqual(state.captureQueue.map((i) => i.provider_conversation_id), ['conv-1', 'conv-2']);
  assert.equal(state.captureQueue[0].messages.length, 2);
});

test('a thinner re-capture never drops messages still waiting to be sent', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  const m = (n) => ({ provider_message_id: `m${n}`, content: `message ${n}` });
  await queue.enqueue({ ...payload, provider_conversation_id: 'thin', messages: [m(1), m(2), m(3)] });
  // Read again before the page finished loading: only the last turn, plus a new one, no title.
  await queue.enqueue({ ...payload, provider_conversation_id: 'thin', title: '', messages: [m(3), m(4)] });
  const item = state.captureQueue.find((i) => i.provider_conversation_id === 'thin');
  assert.deepEqual(item.messages.map((x) => x.provider_message_id), ['m1', 'm2', 'm3', 'm4']);
  assert.equal(item.title, 'Test');
});

test('delivery confirms only what the server stored, at the paired server only', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  await queue.enqueue(payload);
  await queue.drain();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://server.example/api/v1/ingest/conversation');
  assert.equal(state.captureQueue.length, 0);
  assert.equal(state.lastDelivery.chatgpt.conversationId, 'db-1');
  assert.equal(state.lastDelivery.chatgpt.serverUrl, 'https://server.example');
  assert.equal(state.lastDelivery.chatgpt.messages, 1);
  assert.equal(state.delivered['chatgpt:conv-1'], Date.parse(payload.updated_at));
});

test('captures made while a delivery is in flight are kept', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  let release;
  const sending = new Promise((r) => { release = r; });
  respond = async () => {
    await sending;
    return { ok: true, status: 200, url: 'https://server.example/x', json: async () => ({ conversation_id: 'db', messages_inserted: 1 }) };
  };
  await queue.enqueue(payload);
  const draining = queue.drain();
  await tick();
  // A newer capture of the same conversation and a different one, mid-flight.
  await queue.enqueue({ ...payload, title: 'newer' });
  await queue.enqueue({ ...payload, provider_conversation_id: 'conv-2' });
  release();
  await draining;
  // Both the replaced conversation (sent again with its newer content) and conv-2 went out.
  assert.deepEqual(requests.map((r) => [r.body.provider_conversation_id, r.body.title]),
    [['conv-1', 'Test'], ['conv-1', 'newer'], ['conv-2', 'Test']]);
  assert.equal(state.captureQueue.length, 0);
});

test('server errors back off and keep the item; rejected items move to the failed list', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  respond = async () => ({ ok: false, status: 503, text: async () => 'starting up' });
  await queue.enqueue(payload);
  await queue.drain();
  assert.equal(state.captureQueue.length, 1);
  assert.equal(state.captureQueue[0]._attempts, 1);
  assert(state.captureQueue[0]._nextAttemptAt > Date.now());
  assert.match(state.deliveryErrors.chatgpt, /503 starting up/);
  await queue.drain(); // not due yet: nothing is sent
  assert.equal(requests.length, 1);

  respond = async () => ({ ok: false, status: 400, text: async () => '{"error":"unknown provider"}' });
  await queue.enqueue({ ...payload, provider_conversation_id: 'conv-bad' });
  await queue.drain();
  assert.equal(state.deadLetterQueue.length, 1);
  assert.equal(state.deadLetterQueue[0].provider_conversation_id, 'conv-bad');
  assert.match(state.deadLetterQueue[0]._lastError, /unknown provider/);

  respond = async () => ({ ok: true, status: 200, url: 'https://server.example/x', json: async () => ({ conversation_id: 'db', messages_inserted: 1 }) });
  assert.equal(await queue.retryFailed(), 1);
  await queue.drain();
  assert.equal(state.deadLetterQueue.length, 0);
  assert.deepEqual(state.captureQueue.map((i) => i.provider_conversation_id), ['conv-1']);
});

test('a rejected token keeps every item and asks to pair again', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  respond = async () => ({ ok: false, status: 401, text: async () => '{"error":"unauthorized"}' });
  await queue.enqueue(payload);
  await queue.enqueue({ ...payload, provider_conversation_id: 'conv-2' });
  await queue.drain();
  assert.equal(requests.length, 1, 'delivery stops at the first 401');
  assert.equal(state.captureQueue.length, 2);
  assert.equal(state.captureQueue[0]._attempts, 0);
  assert.equal(state.deviceToken, undefined);
  assert.match(state.deliveryErrors.chatgpt, /Pair it again/);
});

// ---------------------------------------------------------------- pairing and server address

test('the server address is normalised and plain http is only for this computer', async () => {
  const api = await import(moduleUrl('lib/api.js'));
  assert.equal(api.normalizeServerUrl('mbp.example.ts.net/'), 'https://mbp.example.ts.net');
  assert.equal(api.normalizeServerUrl(' https://x.example/some/path '), 'https://x.example');
  assert.equal(api.normalizeServerUrl('http://localhost:8080'), 'http://localhost:8080');
  assert.throws(() => api.normalizeServerUrl('http://x.example'), /https/);
  assert.throws(() => api.normalizeServerUrl(''), /server address/);
  assert.equal(api.originPattern('http://localhost:8080'), 'http://localhost/*');
});

test('pairing stores the token with the server that issued it', async () => {
  const api = await import(moduleUrl('lib/api.js'));
  delete state.deviceToken;
  delete state.serverUrl;
  global.navigator = { userAgent: 'test-agent' };
  respond = async () => ({ ok: true, status: 200, json: async () => ({ device_token: 'pcd_new', device_id: 'dev_1' }) });
  await api.pairDevice('mbp.example.ts.net', 'ABCD2345');
  assert.equal(requests[0].url, 'https://mbp.example.ts.net/api/v1/auth/pair-device');
  assert.equal(state.serverUrl, 'https://mbp.example.ts.net');
  assert.equal(state.deviceToken, 'pcd_new');
});

test('an unreachable server is reported, never swapped for another one', async () => {
  const queue = await import(moduleUrl('lib/queue.js'));
  global.fetch = async (url) => { requests.push({ url }); throw new TypeError('Failed to fetch'); };
  await queue.enqueue(payload);
  await queue.drain();
  assert.deepEqual(requests.map((r) => new URL(r.url).origin), ['https://server.example']);
  assert.match(state.deliveryErrors.chatgpt, /Could not reach https:\/\/server\.example/);
  assert.equal(state.captureQueue.length, 1);
});

// ---------------------------------------------------------------- packaging

test('the manifest carries no personal server address and the build ships every file', () => {
  for (const host of manifest.host_permissions) {
    assert.match(host, /^https:\/\/(claude\.ai|chatgpt\.com|gemini\.google\.com|grok\.com|muse\.ai)\/\*$/);
  }
  assert(manifest.permissions.includes('scripting'));
  assert(manifest.permissions.includes('unlimitedStorage'));
  const build = JSON.parse(read('package.json')).scripts.build;
  for (const file of ['manifest.json', 'background.js', 'content.js']) assert(build.includes(file), file);
  for (const dir of ['popup', 'sidepanel', 'providers', 'lib', 'icons']) assert(build.includes(dir), dir);
  const referenced = [manifest.background.service_worker, ...manifest.content_scripts[0].js,
    manifest.action.default_popup, manifest.side_panel.default_path, ...Object.values(manifest.icons)];
  for (const file of referenced) assert(fs.existsSync(path.join(root, file)), file);
});
