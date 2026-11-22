import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const code = await readFile(new URL('../../adapters/gemini/selectors.js', import.meta.url), 'utf8');

const INPUT = `
  <rich-textarea>
    <div class="ql-editor ql-blank" role="textbox" contenteditable="true"><p><br></p></div>
    <div class="ql-clipboard" contenteditable="true"></div>
  </rich-textarea>`;

// Synthetic new-chat /app page shaped like the 2026-11-22 probe; no real Gemini.
const NEW_CHAT_PAGE = `
  <gem-segmented-button class="gem-segmented-button-selected"><button aria-pressed="false">Chat</button></gem-segmented-button>
  <gem-segmented-button><button aria-pressed="true">Task</button></gem-segmented-button>
  <button data-test-id="bard-mode-menu-button" aria-label="Open mode picker, currently Hydrogen"></button>
  ${INPUT}`;

// Inside an existing Task conversation: no mode switch, no model picker.
const CONVERSATION_PAGE = `
  <remy-plan-pill><button class="remy-plan-pill">Complete</button></remy-plan-pill>
  <div class="conversation-container" id="abc123"><user-query></user-query><model-response></model-response></div>
  ${INPUT}`;

function load(html, path = '/app') {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, {
    runScripts: 'outside-only',
    url: 'https://gemini.google.com' + path,
  });
  dom.window.eval(code);
  return dom.window;
}

// Results come from the jsdom realm; copy them into this realm before strict comparison.
function preflight(win) {
  return JSON.parse(JSON.stringify(win.GeminiSelectors.preflight(win.document)));
}

test('new-chat page passes preflight; the Quill clipboard sibling is not mistaken for the input', () => {
  assert.deepEqual(preflight(load(NEW_CHAT_PAGE)), { ok: true, page: 'new_chat', problems: [] });
});

test('conversation page passes without mode switch or model picker', () => {
  assert.deepEqual(preflight(load(CONVERSATION_PAGE, '/app/d0d0d0d0d0d0d0d0')),
    { ok: true, page: 'conversation', problems: [] });
});

test('new-chat page without the mode switch is reported', () => {
  const result = preflight(load(CONVERSATION_PAGE));
  assert.equal(result.page, 'new_chat');
  assert.deepEqual(result.problems.map((p) => [p.name, p.found]), [['modeButton', 0], ['modelMenu', 0]]);
});

test('missing input is reported, not guessed', () => {
  const result = preflight(load(NEW_CHAT_PAGE.replace(/<rich-textarea>[\s\S]*<\/rich-textarea>/, '')));
  assert.equal(result.ok, false);
  assert.deepEqual(result.problems.map((p) => [p.name, p.found]), [['input', 0]]);
});

test('two editors make the input ambiguous', () => {
  const result = preflight(load(NEW_CHAT_PAGE + '<rich-textarea><div class="ql-editor"></div></rich-textarea>'));
  assert.deepEqual(result.problems.map((p) => [p.name, p.found]), [['input', 2]]);
});

test('selector tables are frozen', () => {
  const { GeminiSelectors } = load('');
  assert.ok(Object.isFrozen(GeminiSelectors.SELECTORS));
  assert.ok(Object.isFrozen(GeminiSelectors.PREFLIGHT.conversation[0]));
});
