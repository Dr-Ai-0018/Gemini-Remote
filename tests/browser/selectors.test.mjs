import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const code = await readFile(new URL('../../adapters/gemini/selectors.js', import.meta.url), 'utf8');

// Synthetic idle /app page shaped like the 2026-11-22 probe; no real Gemini.
const IDLE_PAGE = `
  <gem-segmented-button class="gem-segmented-button-selected"><button aria-pressed="false">Chat</button></gem-segmented-button>
  <gem-segmented-button><button aria-pressed="true">Task</button></gem-segmented-button>
  <button data-test-id="bard-mode-menu-button" aria-label="Open mode picker, currently Hydrogen"></button>
  <rich-textarea>
    <div class="ql-editor ql-blank" role="textbox" contenteditable="true"><p><br></p></div>
    <div class="ql-clipboard" contenteditable="true"></div>
  </rich-textarea>`;

function load(html) {
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, { runScripts: 'outside-only' });
  dom.window.eval(code);
  return dom.window;
}

// Results come from the jsdom realm; copy them into this realm before strict comparison.
function preflight(win) {
  return JSON.parse(JSON.stringify(win.GeminiSelectors.preflight(win.document)));
}

test('idle page passes preflight; the Quill clipboard sibling is not mistaken for the input', () => {
  const win = load(IDLE_PAGE);
  assert.deepEqual(preflight(win), { ok: true, problems: [] });
});

test('missing input is reported, not guessed', () => {
  const result = preflight(load(IDLE_PAGE.replace(/<rich-textarea>[\s\S]*<\/rich-textarea>/, '')));
  assert.equal(result.ok, false);
  assert.deepEqual(result.problems.map((p) => [p.name, p.found]), [['input', 0]]);
});

test('two editors make the input ambiguous', () => {
  const result = preflight(load(IDLE_PAGE + '<rich-textarea><div class="ql-editor"></div></rich-textarea>'));
  assert.deepEqual(result.problems.map((p) => [p.name, p.found]), [['input', 2]]);
});

test('selector table is frozen', () => {
  const win = load('');
  assert.ok(Object.isFrozen(win.GeminiSelectors.SELECTORS));
  assert.ok(Object.isFrozen(win.GeminiSelectors.PREFLIGHT[0]));
});
