import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { access, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');
const manifest = JSON.parse(await read('extensions/chromium/manifest.json'));
const buildScript = fileURLToPath(new URL('tools/build-extension.mjs', root));

function build(out, env = {}) {
  return promisify(execFile)(process.execPath, [buildScript, '--out', out], { env: { ...process.env, ...env } });
}

test('extension only runs on Gemini and only talks to loopback', () => {
  assert.deepEqual(manifest.content_scripts.map((c) => c.matches), [['https://gemini.google.com/*']]);
  assert.deepEqual(manifest.permissions, ['storage']);
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*']);
  assert.equal(manifest.action.default_popup, 'popup.html');
});

test('manifest is generic: no per-installation key or CSP', () => {
  assert.equal(manifest.key, undefined);
  assert.equal(manifest.content_security_policy, undefined);
});

test('build is the same for everyone and ignores local settings', async () => {
  const out = await mkdtemp(join(tmpdir(), 'gemini-remote-ext-'));
  try {
    await writeFile(join(out, 'extension-origin.txt'), 'left by an old build\n');
    const { stdout } = await build(out, {
      GEMINI_REMOTE_EXTENSION_KEY: 'not used any more',
      GEMINI_REMOTE_RELAY_PORT: 'not a port',
    });
    assert.match(stdout, new RegExp(`\\(v${manifest.version.replaceAll('.', '\\.')}\\)`));
    const built = JSON.parse(await readFile(join(out, 'manifest.json'), 'utf8'));
    assert.deepEqual(built, manifest);
    const html = await readFile(join(out, 'popup.html'), 'utf8');
    const popupScripts = [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
    for (const name of new Set(['background.js', 'relay-client.js', 'popup.html', ...popupScripts,
      ...manifest.content_scripts[0].js])) {
      await access(join(out, name));
    }
    await assert.rejects(access(join(out, 'extension-origin.txt')));
  } finally {
    await rm(out, { recursive: true, force: true });
  }
});

test('background loads the Relay client it is built with', async () => {
  const background = await read('extensions/chromium/background.js');
  assert.match(background, /importScripts\('relay-client\.js'\)/);
});
