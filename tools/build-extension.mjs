// Assemble the loadable extension in extensions/chromium/dist/.
// The tracked manifest is a template: the per-installation extension key and
// Relay port come from local config (process environment, then the ignored
// .env written by `gemini-remote init-local`), never from tracked files.
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const PREFIX = 'GEMINI_REMOTE_';
const root = new URL('../', import.meta.url);
const source = new URL('extensions/chromium/', root);
const { values: args } = parseArgs({ options: { out: { type: 'string' } } });
const output = args.out ? pathToFileURL(args.out.replace(/[\\/]*$/, '/')) : new URL('dist/', source);

async function localConfig() {
  const values = {};
  try {
    const text = await readFile(new URL('.env', root), 'utf8');
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith('#') || !line.includes('=')) continue;
      const [key, ...rest] = line.split('=');
      if (key.trim().startsWith(PREFIX)) values[key.trim()] = rest.join('=').trim();
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith(PREFIX)) values[key] = value;
  }
  return values;
}

function required(config, name) {
  const value = config[PREFIX + name];
  if (!value) {
    throw new Error(`Missing ${PREFIX}${name}; run \`uv run gemini-remote init-local\` or set it in the environment`);
  }
  return value;
}

// Chromium extension ID: first 16 bytes of SHA-256(public key DER), each
// nibble mapped to a-p.
function extensionIdFromKey(base64Key) {
  const digest = createHash('sha256').update(Buffer.from(base64Key, 'base64')).digest();
  return [...digest.subarray(0, 16)]
    .map((byte) => String.fromCharCode(97 + (byte >> 4), 97 + (byte & 15)))
    .join('');
}

const config = await localConfig();
const key = required(config, 'EXTENSION_KEY');
if (!/^[A-Za-z0-9+/]+={0,2}$/.test(key)) throw new Error(`Invalid ${PREFIX}EXTENSION_KEY: not base64`);
const relayPort = Number(required(config, 'RELAY_PORT'));
if (!Number.isInteger(relayPort) || relayPort < 1 || relayPort > 65535) {
  throw new Error(`Invalid ${PREFIX}RELAY_PORT: ${config[PREFIX + 'RELAY_PORT']}`);
}

const manifest = JSON.parse(await readFile(new URL('manifest.json', source), 'utf8'));
if (manifest.manifest_version !== 3) throw new Error('manifest must be MV3');
if (manifest.key || manifest.content_security_policy) {
  throw new Error('tracked manifest must not carry key or CSP; they come from local config');
}
const pyInit = await readFile(new URL('src/gemini_remote/__init__.py', root), 'utf8');
const pyVersion = /__version__ = "([^"]+)"/.exec(pyInit)?.[1];
if (pyVersion !== manifest.version) {
  throw new Error(`version mismatch: manifest ${manifest.version}, Python package ${pyVersion}`);
}

manifest.key = key;
manifest.content_security_policy = {
  extension_pages: `script-src 'self'; object-src 'none'; connect-src ws://127.0.0.1:${relayPort}`,
};

const files = [
  [new URL('adapters/gemini/selectors.js', root), 'selectors.js'],
  [new URL('background.js', source), 'background.js'],
  [new URL('content-entry.js', source), 'content-entry.js'],
];
await mkdir(output, { recursive: true });
await writeFile(new URL('manifest.json', output), JSON.stringify(manifest, null, 2) + '\n', 'utf8');
for (const [from, name] of files) await copyFile(from, new URL(name, output));

const origin = 'chrome-extension://' + extensionIdFromKey(key);
await writeFile(new URL('extension-origin.txt', output), origin + '\n', 'utf8');
console.log('Built ' + fileURLToPath(output) + ' (v' + manifest.version + ')');
console.log('Relay allowlist origin: ' + origin);
