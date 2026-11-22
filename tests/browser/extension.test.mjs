import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { access, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');
const manifest = JSON.parse(await read('extensions/chromium/manifest.json'));
const buildScript = fileURLToPath(new URL('tools/build-extension.mjs', root));

// Throwaway values standing in for a local .env; the process environment wins
// over the file, so the developer's own .env and dist are never touched.
const TEST_KEY = 'Z2VtaW5pLXJlbW90ZS10ZXN0LWtleQ==';
const TEST_ID = 'adjpkjhmnhmgipgnbfjejnfkbhknkbke';

function build(out, env) {
  return promisify(execFile)(process.execPath, [buildScript, '--out', out], {
    env: { ...process.env, ...env },
  });
}

test('extension only runs on Gemini and only talks to loopback', () => {
  assert.deepEqual(manifest.content_scripts.map((c) => c.matches), [['https://gemini.google.com/*']]);
  assert.deepEqual(manifest.permissions, ['storage']);
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*']);
});

test('tracked manifest carries no per-installation key or port', () => {
  assert.equal(manifest.key, undefined);
  assert.equal(manifest.content_security_policy, undefined);
});

test('build injects key and Relay port from local config', async () => {
  const out = await mkdtemp(join(tmpdir(), 'gemini-remote-ext-'));
  try {
    const { stdout } = await build(out, {
      GEMINI_REMOTE_EXTENSION_KEY: TEST_KEY,
      GEMINI_REMOTE_RELAY_PORT: '23456',
    });
    assert.match(stdout, new RegExp(`\\(v${manifest.version.replaceAll('.', '\\.')}\\)`));
    for (const name of ['manifest.json', 'background.js', ...manifest.content_scripts[0].js]) {
      await access(join(out, name));
    }
    const built = JSON.parse(await readFile(join(out, 'manifest.json'), 'utf8'));
    assert.equal(built.key, TEST_KEY);
    assert.match(built.content_security_policy.extension_pages, /connect-src ws:\/\/127\.0\.0\.1:23456$/);
    assert.equal(await readFile(join(out, 'extension-origin.txt'), 'utf8'), `chrome-extension://${TEST_ID}\n`);
  } finally {
    await rm(out, { recursive: true, force: true });
  }
});

test('build refuses an invalid Relay port', async () => {
  const out = await mkdtemp(join(tmpdir(), 'gemini-remote-ext-'));
  try {
    await assert.rejects(
      build(out, { GEMINI_REMOTE_EXTENSION_KEY: TEST_KEY, GEMINI_REMOTE_RELAY_PORT: '70000' }),
      /Invalid GEMINI_REMOTE_RELAY_PORT/,
    );
  } finally {
    await rm(out, { recursive: true, force: true });
  }
});
