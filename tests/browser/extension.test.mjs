import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { access, readFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');
const manifest = JSON.parse(await read('extensions/chromium/manifest.json'));

test('extension only runs on Gemini and only talks to loopback', () => {
  assert.deepEqual(manifest.content_scripts.map((c) => c.matches), [['https://gemini.google.com/*']]);
  assert.deepEqual(manifest.permissions, ['storage']);
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*']);
});

test('CSP allows exactly the Relay port configured on the Python side', async () => {
  const settings = await read('src/gemini_remote/config/settings.py');
  const port = /DEFAULT_RELAY_PORT = (\d+)/.exec(settings)[1];
  assert.match(manifest.content_security_policy.extension_pages, new RegExp(`connect-src ws://127\\.0\\.0\\.1:${port}$`));
});

test('build produces a loadable dist with a stable extension origin', async () => {
  const { stdout } = await promisify(execFile)(process.execPath, [fileURLToPath(new URL('tools/build-extension.mjs', root))]);
  assert.match(stdout, new RegExp(`\\(v${manifest.version.replaceAll('.', '\\.')}\\)`));
  const dist = 'extensions/chromium/dist/';
  for (const name of ['manifest.json', 'background.js', ...manifest.content_scripts[0].js]) {
    await access(new URL(dist + name, root));
  }
  assert.match(await read(dist + 'extension-origin.txt'), /^chrome-extension:\/\/[a-p]{32}\n$/);
});
