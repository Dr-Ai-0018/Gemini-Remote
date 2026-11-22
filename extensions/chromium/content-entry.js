// Content script on Gemini pages. Read-only toward the page: runs the selector
// preflight, reports it to the background over a Port, and shows the floating
// Gemini Remote button + panel inside a closed shadow root (so neither Gemini's
// styles nor the adapter's selectors ever see it). No typing, no clicking.
(function () {
  'use strict';

  const TAG = '[gemini-remote]';
  const POLL_MS = 1000;
  const MAX_WAIT_MS = 30000;
  const KEEPALIVE_MS = 20000;
  const version = chrome.runtime.getManifest().version;

  let port = null;
  let preflight = { ok: false, pending: true, page: GeminiSelectors.pageKind(location.pathname), problems: [] };
  let lastState = null;

  // --- preflight (re-run when Gemini navigates inside the single-page app) ---

  let checkTimer = null;
  function runPreflight() {
    clearTimeout(checkTimer);
    const started = Date.now();
    const path = location.pathname;
    const check = () => {
      const result = GeminiSelectors.preflight(document);
      // The page can be connected but not rendered yet; re-check before failing.
      if (!result.ok && Date.now() - started < MAX_WAIT_MS && location.pathname === path) {
        setPreflight({ ...result, pending: true });
        checkTimer = setTimeout(check, POLL_MS);
        return;
      }
      if (result.ok) console.info(TAG, version, 'preflight ok', result.page);
      else console.warn(TAG, version, 'preflight failed', result.page, result.problems);
      setPreflight(result);
    };
    check();
  }

  function setPreflight(result) {
    const changed = JSON.stringify(result) !== JSON.stringify(preflight);
    preflight = result;
    if (!changed) return;
    post({ type: 'preflight', result: preflight.pending ? { ...preflight, ok: false } : preflight });
    if (lastState) panel.update(lastState, preflight);
  }

  let lastPath = location.pathname;
  setInterval(() => {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      runPreflight();
    }
  }, 2000);

  // --- Port to the background (reconnects if the service worker restarts) ---

  function post(message) {
    if (!port) return;
    try {
      port.postMessage(message);
    } catch {
      port = null;
    }
  }

  function connectPort() {
    try {
      port = chrome.runtime.connect({ name: 'gemini-page' });
    } catch {
      port = null; // extension reloaded or removed: this old script is orphaned
      return;
    }
    port.onMessage.addListener((message) => {
      if (message && message.type === 'state') render(message.state);
    });
    port.onDisconnect.addListener(() => {
      port = null;
      setTimeout(connectPort, 1000);
    });
    post({ type: 'preflight', result: preflight.pending ? { ...preflight, ok: false } : preflight });
  }

  setInterval(() => post({ type: 'keepalive' }), KEEPALIVE_MS);

  function request(message) {
    return chrome.runtime.sendMessage(message).catch((error) => ({ ok: false, error: String(error) }));
  }

  // --- floating button + panel ---

  const host = document.createElement('gemini-remote-panel');
  host.style.cssText = 'all: initial; position: fixed; right: 20px; bottom: 120px; z-index: 2147483646;';
  const shadow = host.attachShadow({ mode: 'closed' });
  const api = {
    pair: (code) => request({ type: 'pair', code }),
    unpair: () => request({ type: 'unpair' }),
    setPort: (value) => request({ type: 'set_port', port: value }),
    reconnect: () => request({ type: 'reconnect' }),
  };
  const panel = GeminiRemotePanel.createPanel(document, api, { onClose: () => toggle(false) });
  const style = document.createElement('style');
  style.textContent = panel.style + `
    .fab { position: relative; width: 40px; height: 40px; border-radius: 50%; border: 1px solid #d0d0d0;
      background: #fff; color: #0b57d0; font: 600 13px/38px system-ui, sans-serif; text-align: center;
      padding: 0; cursor: pointer; box-shadow: 0 2px 10px rgba(0,0,0,.18); }
    .fab .badge { position: absolute; right: 1px; top: 1px; width: 10px; height: 10px; border-radius: 50%;
      border: 2px solid #fff; background: #9e9e9e; }
    .fab.ok .badge { background: #1e8e3e; } .fab.wait .badge { background: #f29900; }
    .fab.bad .badge { background: #d93025; }
    .pop { position: absolute; right: 0; bottom: 52px; }
    @media (prefers-color-scheme: dark) {
      .fab { background: #2a2a2a; border-color: #444; color: #a8c7fa; } .fab .badge { border-color: #2a2a2a; }
    }`;
  const fab = document.createElement('button');
  fab.className = 'fab';
  fab.title = 'Gemini Remote';
  fab.setAttribute('aria-label', 'Gemini Remote');
  fab.append('GR', Object.assign(document.createElement('span'), { className: 'badge' }));
  const pop = document.createElement('div');
  pop.className = 'pop';
  pop.hidden = true;
  pop.append(panel.element);
  shadow.append(style, pop, fab);

  function toggle(open = pop.hidden) {
    pop.hidden = !open;
    if (open) request({ type: 'get_state' }).then((state) => state && state.relay && render(state));
  }
  fab.addEventListener('click', () => toggle());
  shadow.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') toggle(false);
  });

  function render(state) {
    lastState = state;
    const level = state.relay === 'connected' ? (preflight.ok ? 'ok' : 'wait')
      : state.relay === 'offline' || state.relay === 'unauthorized' ? 'bad' : 'wait';
    fab.className = `fab ${level}`;
    panel.update(state, preflight);
    // First visit with nothing paired: open the panel once so the user sees where to pair.
    if (!state.paired && !render.prompted) {
      render.prompted = true;
      pop.hidden = false;
    }
  }

  document.documentElement.append(host);
  connectPort();
  runPreflight();
})();
