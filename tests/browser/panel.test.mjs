// Floating button + panel in jsdom, with fake chrome.runtime plumbing.
import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), 'utf8');
const SELECTORS = await read('adapters/gemini/selectors.js');
const PANEL = await read('extensions/chromium/panel.js');
const CONTENT = await read('extensions/chromium/content-entry.js');

const STATE = {
  relay: 'connected', relayVersion: '0.1.4', error: null, retryAt: null, port: 18776, paired: true,
  extensionVersion: '0.1.4', tasksEnabled: false, tabs: { open: 1, ready: 1 },
};

const windows = [];
after(() => windows.forEach((w) => w.close())); // the content script's timers would keep node alive

function page(path = '/app/abc') {
  const dom = new JSDOM('<!doctype html><body><rich-textarea><div class="ql-editor" role="textbox" contenteditable="true"></div></rich-textarea></body>', {
    runScripts: 'outside-only', url: 'https://gemini.google.com' + path,
  });
  windows.push(dom.window);
  return dom.window;
}

function panelIn(window, api = {}) {
  window.eval(PANEL);
  const { createPanel } = window.GeminiRemotePanel;
  const calls = [];
  const fake = new Proxy({}, {
    get: (_, name) => async (...args) => {
      calls.push([name, ...args]);
      return api[name] ? api[name](...args) : { ok: true };
    },
  });
  const panel = createPanel(window.document, fake);
  window.document.body.append(panel.element);
  return { panel, calls, $: (sel) => panel.element.querySelector(sel), text: () => panel.element.textContent };
}

const visible = (node) => {
  for (let n = node; n; n = n.parentElement) if (n.hidden) return false;
  return true;
};
const button = (root, label) => [...root.querySelectorAll('button')].find((b) => b.textContent === label);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const plain = (value) => JSON.parse(JSON.stringify(value)); // objects made inside the jsdom realm

test('connected and paired: shows versions, hides the pairing form', () => {
  const window = page();
  const { panel, $, text } = panelIn(window);
  panel.update(STATE, { ok: true, page: 'conversation', problems: [] });
  assert.match(text(), /已连接 Relay 0\.1\.4/);
  assert.match(text(), /v0\.1\.4/);
  assert.match(text(), /本页（对话页）结构检查通过/);
  assert.match(text(), /执行任务：下一版本开放/);
  assert.ok(!visible($('input.code')));
  assert.ok(visible(button(panel.element, '解除配对')));
  assert.equal($('input[type=number]').value, '18776');
});

test('unpaired: pairing form is shown and a code is sent to the background', async () => {
  const window = page();
  const { panel, calls, $ } = panelIn(window, {
    pair: () => ({ ok: true, state: { ...STATE } }),
  });
  panel.update({ ...STATE, relay: 'unpaired', paired: false, relayVersion: null }, null);
  assert.ok(visible($('input.code')));
  $('input.code').value = '123 456';
  button(panel.element, '配对').click();
  await tick();
  assert.deepEqual(calls[0], ['pair', '123 456']);
  assert.equal($('input.code').value, '');
  assert.ok(!visible($('input.code')));
});

test('pairing errors are explained', async () => {
  const window = page();
  const { panel, $, text } = panelIn(window, { pair: () => ({ ok: false, error: 'no_code' }) });
  panel.update({ ...STATE, relay: 'unpaired', paired: false }, null);
  $('input.code').value = '123456';
  button(panel.element, '配对').click();
  await tick();
  assert.match(text(), /gemini-remote pair 生成新的/);
});

test('refused token asks to pair again', () => {
  const window = page();
  const { panel, $, text } = panelIn(window);
  panel.update({ ...STATE, relay: 'unauthorized', relayVersion: null }, null);
  assert.match(text(), /配对已失效/);
  assert.ok(visible($('input.code')));
});

test('offline shows where it dials and offers a retry', async () => {
  const window = page();
  const { panel, calls, text } = panelIn(window);
  panel.update({ ...STATE, relay: 'offline', relayVersion: null, port: 23456, retryAt: Date.now() + 5000 }, null);
  assert.match(text(), /连不上 Relay（127\.0\.0\.1:23456），\d 秒后重试/);
  button(panel.element, '立即重试').click();
  await tick();
  assert.deepEqual(calls, [['reconnect']]);
});

test('version mismatch is called out', () => {
  const window = page();
  const { panel, text } = panelIn(window);
  panel.update({ ...STATE, relayVersion: '0.1.3' }, null);
  assert.match(text(), /扩展 0\.1\.4 和 Relay 0\.1\.3 版本不一致/);
});

test('unpair needs a second click', async () => {
  const window = page();
  const { panel, calls } = panelIn(window);
  panel.update(STATE, null);
  button(panel.element, '解除配对').click();
  await tick();
  assert.deepEqual(calls, []);
  button(panel.element, '确认解除？').click();
  await tick();
  assert.deepEqual(calls, [['unpair']]);
});

test('port is validated by the background and errors are shown', async () => {
  const window = page();
  const { panel, calls, $, text } = panelIn(window, { setPort: () => ({ ok: false, error: 'port_invalid' }) });
  panel.update(STATE, null);
  $('input[type=number]').value = '70000';
  button(panel.element, '保存').click();
  await tick();
  assert.deepEqual(calls, [['setPort', 70000]]);
  assert.match(text(), /1–65535/);
});

test('popup view summarises Gemini tabs', () => {
  const window = page();
  const { panel, text } = panelIn(window);
  panel.update({ ...STATE, tabs: { open: 2, ready: 1 } }, null);
  assert.match(text(), /打开 2 个，1 个结构检查通过/);
  panel.update({ ...STATE, tabs: { open: 0, ready: 0 } }, null);
  assert.match(text(), /没有打开的 Gemini 标签页/);
});

// --- content script ----------------------------------------------------------

function contentScript(path = '/app/abc') {
  const window = page(path);
  const posted = [];
  const sent = [];
  const listeners = { message: [], disconnect: [] };
  window.chrome = {
    runtime: {
      getManifest: () => ({ version: '0.1.4' }),
      connect: ({ name }) => ({
        name,
        postMessage: (m) => posted.push(m),
        onMessage: { addListener: (fn) => listeners.message.push(fn) },
        onDisconnect: { addListener: (fn) => listeners.disconnect.push(fn) },
      }),
      sendMessage: async (m) => {
        sent.push(m);
        return m.type === 'get_state' ? STATE : { ok: true };
      },
    },
  };
  let shadow = null;
  const attach = window.Element.prototype.attachShadow;
  window.Element.prototype.attachShadow = function (init) {
    shadow = attach.call(this, { ...init, mode: 'open' }); // closed in real use; opened here to look inside
    return shadow;
  };
  window.eval(SELECTORS);
  window.eval(PANEL);
  window.eval(CONTENT);
  const push = (state) => listeners.message.forEach((fn) => fn({ type: 'state', state }));
  return { window, posted, sent, push, shadow: () => shadow };
}

test('content script reports preflight and draws the button outside the page DOM', () => {
  const { window, posted, push, shadow } = contentScript();
  assert.deepEqual(plain(posted.at(-1)), { type: 'preflight', result: { ok: true, page: 'conversation', problems: [] } });
  const host = window.document.querySelector('gemini-remote-panel');
  assert.equal(host.parentElement, window.document.documentElement);
  assert.equal(window.document.body.querySelector('gemini-remote-panel'), null);
  push(STATE);
  const fab = shadow().querySelector('.fab');
  assert.equal(fab.className, 'fab ok');
  assert.ok(shadow().querySelector('.pop').hidden); // paired: stays out of the way
});

test('first visit without pairing opens the panel once', () => {
  const { push, shadow } = contentScript();
  push({ ...STATE, relay: 'unpaired', paired: false, relayVersion: null });
  const pop = shadow().querySelector('.pop');
  assert.ok(!pop.hidden);
  assert.equal(shadow().querySelector('.fab').className, 'fab wait');
  shadow().querySelector('.card button[aria-label="收起"]').click();
  push({ ...STATE, relay: 'unpaired', paired: false, relayVersion: null });
  assert.ok(pop.hidden);
});

test('content script asks the background, never the Relay, to pair', async () => {
  const { push, sent, shadow } = contentScript();
  push({ ...STATE, relay: 'unpaired', paired: false });
  shadow().querySelector('input.code').value = '654321';
  button(shadow(), '配对').click();
  await tick();
  assert.deepEqual(plain(sent.at(-1)), { type: 'pair', code: '654321' });
});
