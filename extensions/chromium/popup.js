// Toolbar popup: the same panel as on Gemini pages, as a backup entry point.
'use strict';

(function () {
  const request = (message) =>
    chrome.runtime.sendMessage(message).catch((error) => ({ ok: false, error: String(error) }));
  const api = {
    pair: (code) => request({ type: 'pair', code }),
    unpair: () => request({ type: 'unpair' }),
    setPort: (value) => request({ type: 'set_port', port: value }),
    reconnect: () => request({ type: 'reconnect' }),
  };
  const panel = GeminiRemotePanel.createPanel(document, api);
  const style = document.createElement('style');
  style.textContent = panel.style.replace(':host { all: initial; }', '') +
    '.card { border: none; box-shadow: none; border-radius: 0; }';
  document.head.append(style);
  document.body.append(panel.element);

  async function refresh() {
    const state = await request({ type: 'get_state' });
    if (state && state.relay) panel.update(state, null);
  }
  refresh();
  setInterval(refresh, 1000); // the popup lives only while it is open
})();
