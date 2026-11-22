// MV3 service worker: owns the single connection to the local Relay and the
// list of open Gemini tabs. The panel on the page and the toolbar popup are
// only views of this state; pairing and the Relay port are set from them.
'use strict';

importScripts('relay-client.js');

const VERSION = chrome.runtime.getManifest().version;
// The Gemini adapter (typing, sending, reading results) lands in M6. Until then
// the extension never reports ready, so the Relay never hands it a task.
const TASKS_ENABLED = false;

const pages = new Map(); // content-script Port -> { page, ok, problems } | null

const client = new GeminiRelayClient.RelayClient({
  WebSocket,
  version: VERSION,
  storage: {
    get: (keys) => chrome.storage.local.get(keys),
    set: (values) => chrome.storage.local.set(values),
    remove: (keys) => chrome.storage.local.remove(keys),
  },
  onChange: broadcast,
  onCommand: (command) => {
    console.warn('[gemini-remote] task received before the adapter exists; ignoring', command.job_id);
  },
});
const started = client.start();

function snapshot() {
  const tabs = [...pages.values()];
  return {
    ...client.snapshot(),
    tasksEnabled: TASKS_ENABLED,
    tabs: { open: tabs.length, ready: tabs.filter((t) => t && t.ok).length },
  };
}

function broadcast() {
  const state = snapshot();
  for (const port of pages.keys()) {
    try {
      port.postMessage({ type: 'state', state });
    } catch {
      pages.delete(port);
    }
  }
}

function pagesChanged() {
  client.setBrowserReady(TASKS_ENABLED && [...pages.values()].some((p) => p && p.ok));
  broadcast();
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'gemini-page') return;
  pages.set(port, null);
  port.onMessage.addListener((message) => {
    if (message && message.type === 'preflight') {
      pages.set(port, message.result);
      pagesChanged();
    } // 'keepalive' needs no answer: receiving it is what keeps the worker alive
  });
  port.onDisconnect.addListener(() => {
    pages.delete(port);
    pagesChanged();
  });
  started.then(() => {
    client.connect(); // the worker may have slept; dial again if needed
    pagesChanged();
  });
});

async function handle(message) {
  await started;
  switch (message.type) {
    case 'get_state':
      return snapshot();
    case 'pair':
      return { ...(await client.pair(message.code)), state: snapshot() };
    case 'unpair':
      await client.unpair();
      return { ok: true, state: snapshot() };
    case 'set_port':
      return { ...(await client.setPort(message.port)), state: snapshot() };
    case 'reconnect':
      client.connect();
      return { ok: true, state: snapshot() };
    case 'runtime_version':
      return { version: VERSION };
    default:
      return { ok: false, error: 'unknown_request' };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !message || typeof message.type !== 'string') return false;
  handle(message).then(sendResponse, (error) => sendResponse({ ok: false, error: String(error) }));
  return true; // answered asynchronously
});

chrome.runtime.onInstalled.addListener(() => {
  console.info('[gemini-remote] extension', VERSION, 'installed/reloaded');
});
