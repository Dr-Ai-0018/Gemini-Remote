// Every Gemini page selector lives here. When Google changes the page, only
// this file (and the adapter that reads it) should need to change.
// Snapshot of the page structure as of 2026-11-22. aria-label values assume
// the English UI.
(function (root) {
  'use strict';

  const SELECTORS = Object.freeze({
    // Page-level. The input is always there; the mode switch and model picker
    // only on a new-chat page.
    input: 'rich-textarea .ql-editor',
    modeButton: 'gem-segmented-button',
    modeButtonSelected: 'gem-segmented-button.gem-segmented-button-selected',
    modelMenu: '[data-test-id="bard-mode-menu-button"]',
    // Appear only in certain states.
    send: 'button[aria-label="Send message"]',
    stop: 'button[aria-label="Stop response"]',
    planPill: 'remy-plan-pill button.remy-plan-pill',
    chatHistory: 'infinite-scroller.chat-history',
    // Per turn: div.conversation-container#<turn id>.
    turn: 'div.conversation-container',
    userQuery: 'user-query',
    userQueryLine: 'user-query p.query-text-line',
    modelResponse: 'model-response',
    messageContent: 'message-content',
    canvasChip: 'immersive-entry-chip',
    canvasEditor: 'immersive-editor',
    messageActions: 'message-actions',
    responseFooter: '.response-footer',
  });

  // What a logged-in page must show before the adapter may act. The Chat/Task
  // switch and the model picker exist only on a new-chat page (/app); inside an
  // existing conversation (/app/<id>) both are gone, so only the input is
  // required there.
  const PREFLIGHT = Object.freeze({
    new_chat: Object.freeze([
      Object.freeze({ name: 'input', expected: 1 }),
      Object.freeze({ name: 'modeButton', expected: 2 }),
      Object.freeze({ name: 'modelMenu', expected: 1 }),
    ]),
    conversation: Object.freeze([
      Object.freeze({ name: 'input', expected: 1 }),
    ]),
  });

  function pageKind(pathname) {
    return /^\/app\/?$/.test(pathname || '') ? 'new_chat' : 'conversation';
  }

  // Read-only: counts matches, never clicks or types. Missing or ambiguous
  // structure is reported instead of guessed around.
  function preflight(doc, pathname = doc.location && doc.location.pathname) {
    const page = pageKind(pathname);
    const problems = [];
    for (const check of PREFLIGHT[page]) {
      const selector = SELECTORS[check.name];
      const found = doc.querySelectorAll(selector).length;
      if (found !== check.expected) {
        problems.push({ name: check.name, selector, expected: check.expected, found });
      }
    }
    return { ok: problems.length === 0, page, problems };
  }

  root.GeminiSelectors = Object.freeze({ SELECTORS, PREFLIGHT, pageKind, preflight });
})(globalThis);
