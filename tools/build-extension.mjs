// Assemble the loadable extension in extensions/chromium/dist/.
// Fails if the manifest identity is missing or the release versions disagree.
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = new URL('../', import.meta.url);
const source = new URL('extensions/chromium/', root);
const output = new URL('dist/', source);

const manifest = JSON.parse(await readFile(new URL('manifest.json', source), 'utf8'));
if (manifest.manifest_version !== 3 || !manifest.key) {
  throw new Error('manifest must be MV3 and carry a public key for a stable extension ID');
}
const pyInit = await readFile(new URL('src/gemini_remote/__init__.py', root), 'utf8');
const pyVersion = /__version__ = "([^"]+)"/.exec(pyInit)?.[1];
if (pyVersion !== manifest.version) {
  throw new Error(`version mismatch: manifest ${manifest.version}, Python package ${pyVersion}`);
}

const files = [
  [new URL('adapters/gemini/selectors.js', root), 'selectors.js'],
  [new URL('background.js', source), 'background.js'],
  [new URL('content-entry.js', source), 'content-entry.js'],
];
await mkdir(output, { recursive: true });
await writeFile(new URL('manifest.json', output), JSON.stringify(manifest, null, 2) + '\n', 'utf8');
for (const [from, name] of files) await copyFile(from, new URL(name, output));

// Chromium extension ID: first 16 bytes of SHA-256(public key DER), each
// nibble mapped to a-p.
function extensionIdFromKey(base64Key) {
  const digest = createHash('sha256').update(Buffer.from(base64Key, 'base64')).digest();
  return [...digest.subarray(0, 16)]
    .map((byte) => String.fromCharCode(97 + (byte >> 4), 97 + (byte & 15)))
    .join('');
}

const origin = 'chrome-extension://' + extensionIdFromKey(manifest.key);
await writeFile(new URL('extension-origin.txt', output), origin + '\n', 'utf8');
console.log('Built ' + fileURLToPath(output) + ' (v' + manifest.version + ')');
console.log('Relay allowlist origin: ' + origin);
