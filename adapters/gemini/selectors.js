// Every Gemini page selector lives here. When Google changes the page, only
// this file (and the adapter that reads it) should need to change.
// Snapshot: design/gemini-page-probe-2026-11-22.md. aria-label values assume
// the English UI.
(function (root) {
  'use strict';

  const SELECTORS = Object.freeze({
    // Page-level, present on an idle /app page.
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

  // What an idle, logged-in /app page must show before the adapter may act.
  const PREFLIGHT = Object.freeze([
    Object.freeze({ name: 'input', expected: 1 }),
    Object.freeze({ name: 'modeButton', expected: 2 }),
    Object.freeze({ name: 'modelMenu', expected: 1 }),
  ]);

  // Read-only: counts matches, never clicks or types. Missing or ambiguous
  // structure is reported instead of guessed around.
  function preflight(doc) {
    const problems = [];
    for (const check of PREFLIGHT) {
      const selector = SELECTORS[check.name];
      const found = doc.querySelectorAll(selector).length;
      if (found !== check.expected) {
        problems.push({ name: check.name, selector, expected: check.expected, found });
      }
    }
    return { ok: problems.length === 0, problems };
  }

  root.GeminiSelectors = Object.freeze({ SELECTORS, PREFLIGHT, preflight });
})(globalThis);
