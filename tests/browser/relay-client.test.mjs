// RelayClient against a scripted fake socket: pairing, reconnect, keepalive,
// version report. The real Relay side is covered by the Python integration tests.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const code = await readFile(new URL('../../extensions/chromium/relay-client.js', import.meta.url), 'utf8');
const sandbox = {};
new Function('globalThis', code)(sandbox); // same realm, so deepEqual works on its objects
const { RelayClient, PING_MS, BACKOFF_MS, KEYS, DEFAULT_PORT } = sandbox.GeminiRelayClient;

class FakeTimers {
  constructor() {
    this.next = 1;
    this.jobs = new Map();
  }
  setTimeout(fn, ms) {
    const id = this.next++;
    this.jobs.set(id, { fn, ms, repeat: false });
    return id;
  }
  setInterval(fn, ms) {
    const id = this.next++;
    this.jobs.set(id, { fn, ms, repeat: true });
    return id;
  }
  clearTimeout(id) {
    this.jobs.delete(id);
  }
  clearInterval(id) {
    this.jobs.delete(id);
  }
  pending(ms) {
    return [...this.jobs.values()].filter((j) => j.ms === ms);
  }
  fire(ms) {
    for (const [id, job] of [...this.jobs]) {
      if (job.ms !== ms) continue;
      if (!job.repeat) this.jobs.delete(id);
      job.fn();
    }
  }
}

function harness(saved = {}) {
  const sockets = [];
  class FakeSocket {
    constructor(url) {
      this.url = url;
      this.sent = [];
      this.closed = null;
      sockets.push(this);
    }
    send(data) {
      this.sent.push(JSON.parse(data));
    }
    close(code, reason) {
      this.closed = { code, reason };
    }
    // test side
    open() {
      this.onopen();
    }
    receive(message) {
      this.onmessage({ data: JSON.stringify(message) });
    }
    drop(code = 1006, reason = '') {
      this.onclose({ code, reason });
    }
  }
  const store = { ...saved };
  const storage = {
    get: async (keys) => Object.fromEntries(keys.filter((k) => k in store).map((k) => [k, store[k]])),
    set: async (values) => Object.assign(store, values),
    remove: async (keys) => keys.forEach((k) => delete store[k]),
  };
  const timers = new FakeTimers();
  const changes = [];
  const commands = [];
  const client = new RelayClient({
    WebSocket: FakeSocket, storage, version: '0.1.4', timers,
    onChange: (s) => changes.push(s), onCommand: (c) => commands.push(c),
  });
  return { client, sockets, store, timers, changes, commands, last: () => sockets.at(-1) };
}

async function connected(h) {
  await h.client.start();
  const ws = h.last();
  ws.open();
  ws.receive({ type: 'welcome', relay_version: '0.1.4' });
  return ws;
}

test('without a token it waits for pairing and dials nothing', async () => {
  const h = harness();
  await h.client.start();
  assert.equal(h.sockets.length, 0);
  assert.equal(h.client.snapshot().relay, 'unpaired');
  assert.equal(h.client.snapshot().port, DEFAULT_PORT);
});

test('with a token it says hello with its version and reports status', async () => {
  const h = harness({ [KEYS.token]: 'tok', [KEYS.port]: 23456 });
  const ws = await connected(h);
  assert.equal(ws.url, 'ws://127.0.0.1:23456');
  assert.deepEqual(ws.sent[0], { type: 'hello', token: 'tok', extension_version: '0.1.4' });
  assert.deepEqual(ws.sent[1], { type: 'status', browser_ready: false });
  assert.deepEqual({ ...h.client.snapshot(), retryAt: null },
    { relay: 'connected', relayVersion: '0.1.4', error: null, retryAt: null, port: 23456, paired: true,
      extensionVersion: '0.1.4' });
});

test('pairing stores the token and connects', async () => {
  const h = harness();
  await h.client.start();
  const result = h.client.pair(' 123 456 ');
  const ws = h.last();
  ws.open();
  assert.deepEqual(ws.sent[0], { type: 'pair', code: '123456', extension_version: '0.1.4' });
  ws.receive({ type: 'paired', token: 'new-token' });
  ws.receive({ type: 'welcome', relay_version: '0.1.4' });
  assert.deepEqual(await result, { ok: true });
  assert.equal(h.store[KEYS.token], 'new-token');
  assert.equal(h.client.snapshot().relay, 'connected');
});

test('wrong code is reported and nothing is stored', async () => {
  const h = harness();
  await h.client.start();
  const result = h.client.pair('000000');
  h.last().open();
  h.last().drop(1008, 'bad_code');
  assert.deepEqual(await result, { ok: false, error: 'bad_code' });
  assert.equal(h.client.snapshot().relay, 'unpaired');
  assert.equal(h.store[KEYS.token], undefined);
  assert.equal(h.timers.jobs.size, 0); // no retry loop without a token
});

test('malformed codes never reach the Relay', async () => {
  const h = harness();
  await h.client.start();
  assert.deepEqual(await h.client.pair('12345'), { ok: false, error: 'code_format' });
  assert.deepEqual(await h.client.pair('abcdef'), { ok: false, error: 'code_format' });
  assert.equal(h.sockets.length, 0);
});

test('unreachable Relay during pairing is reported', async () => {
  const h = harness();
  await h.client.start();
  const result = h.client.pair('123456');
  h.last().drop(1006);
  assert.deepEqual(await result, { ok: false, error: 'relay_unreachable' });
});

test('failed re-pairing falls back to the existing token', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  const first = await connected(h);
  const result = h.client.pair('000000');
  assert.equal(first.closed.code, 1000);
  h.last().open();
  h.last().drop(1008, 'no_code');
  assert.deepEqual(await result, { ok: false, error: 'no_code' });
  const again = h.last();
  again.open();
  assert.deepEqual(again.sent[0], { type: 'hello', token: 'tok', extension_version: '0.1.4' });
});

test('dropped connection is retried with growing backoff and recovers', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  const ws = await connected(h);
  ws.drop(1006);
  assert.equal(h.client.snapshot().relay, 'offline');
  assert.equal(h.timers.pending(BACKOFF_MS[0]).length, 1);
  h.timers.fire(BACKOFF_MS[0]);
  h.last().drop(1006); // Relay still down
  assert.equal(h.timers.pending(BACKOFF_MS[1]).length, 1);
  h.timers.fire(BACKOFF_MS[1]);
  h.last().open();
  h.last().receive({ type: 'welcome', relay_version: '0.1.4' });
  assert.equal(h.client.snapshot().relay, 'connected');
  h.last().drop(1006);
  assert.equal(h.timers.pending(BACKOFF_MS[0]).length, 1); // backoff reset after a good session
});

test('backoff is capped', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  await h.client.start();
  for (let i = 0; i < BACKOFF_MS.length + 3; i += 1) {
    h.last().drop(1006);
    const delay = BACKOFF_MS[Math.min(i, BACKOFF_MS.length - 1)];
    assert.equal(h.timers.pending(delay).length, 1, `attempt ${i}`);
    h.timers.fire(delay);
  }
});

test('refused token stops retrying until the user pairs again', async () => {
  const h = harness({ [KEYS.token]: 'old' });
  await h.client.start();
  h.last().open();
  h.last().drop(1008, 'unauthorized');
  assert.equal(h.client.snapshot().relay, 'unauthorized');
  assert.equal(h.timers.jobs.size, 0);
  h.client.connect(); // e.g. a Gemini tab opened
  assert.equal(h.sockets.length, 1);
});

test('busy Relay (another connection) is retried', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  await h.client.start();
  h.last().open();
  h.last().drop(1013, 'busy');
  assert.equal(h.client.snapshot().relay, 'offline');
  assert.equal(h.timers.pending(BACKOFF_MS[0]).length, 1);
});

test('keepalive pings while connected and stops after a drop', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  const ws = await connected(h);
  h.timers.fire(PING_MS);
  h.timers.fire(PING_MS);
  assert.deepEqual(ws.sent.slice(-2), [{ type: 'ping' }, { type: 'ping' }]);
  ws.drop(1006);
  assert.equal(h.timers.pending(PING_MS).length, 0);
});

test('readiness changes are sent once, and only while connected', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  const ws = await connected(h);
  h.client.setBrowserReady(true);
  h.client.setBrowserReady(true);
  assert.deepEqual(ws.sent.filter((m) => m.type === 'status').map((m) => m.browser_ready), [false, true]);
  ws.drop(1006);
  h.client.setBrowserReady(false);
  h.timers.fire(BACKOFF_MS[0]);
  h.last().open();
  h.last().receive({ type: 'welcome', relay_version: '0.1.4' });
  assert.deepEqual(h.last().sent[1], { type: 'status', browser_ready: false }); // re-announced on reconnect
});

test('commands from the Relay are passed on', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  const ws = await connected(h);
  ws.receive({ type: 'command', job_id: 'j1', task: 't', mode: 'task', deadline_at: 1 });
  assert.equal(h.commands[0].job_id, 'j1');
});

test('unpair forgets the token and closes the socket', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  const ws = await connected(h);
  await h.client.unpair();
  assert.equal(ws.closed.code, 1000);
  assert.equal(h.store[KEYS.token], undefined);
  assert.equal(h.client.snapshot().relay, 'unpaired');
  ws.drop(1000); // the late close event of the old socket changes nothing
  assert.equal(h.client.snapshot().relay, 'unpaired');
  assert.equal(h.timers.jobs.size, 0);
});

test('changing the port reconnects there, even after a refusal', async () => {
  const h = harness({ [KEYS.token]: 'tok' });
  await h.client.start();
  h.last().open();
  h.last().drop(1008, 'unauthorized');
  assert.deepEqual(await h.client.setPort(70000), { ok: false, error: 'port_invalid' });
  assert.deepEqual(await h.client.setPort(23456), { ok: true });
  assert.equal(h.store[KEYS.port], 23456);
  assert.equal(h.last().url, 'ws://127.0.0.1:23456');
  assert.equal(h.client.snapshot().relay, 'connecting');
});
