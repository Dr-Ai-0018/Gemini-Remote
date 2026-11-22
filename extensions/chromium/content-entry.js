// Content script entry. Read-only for now: waits for the page to render, runs
// the selector preflight once and logs the result. No typing, no clicking.
(function () {
  'use strict';

  const TAG = '[gemini-remote]';
  const POLL_MS = 1000;
  const MAX_WAIT_MS = 30000;
  const version = chrome.runtime.getManifest().version;
  const started = Date.now();

  function check() {
    const result = globalThis.GeminiSelectors.preflight(document);
    if (result.ok) {
      console.info(TAG, version, 'preflight ok');
      return;
    }
    // The page can be connected but not rendered yet; re-check before failing.
    if (Date.now() - started < MAX_WAIT_MS) {
      setTimeout(check, POLL_MS);
      return;
    }
    console.warn(TAG, version, 'preflight failed', result.problems);
  }

  check();
})();
