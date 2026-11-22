// Assemble the loadable extension in extensions/chromium/dist/.
// The build is generic: no per-installation key, port or token. The extension
// pairs with the local Relay after install, from the panel on the Gemini page.
import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const root = new URL('../', import.meta.url);
const source = new URL('extensions/chromium/', root);
const { values: args } = parseArgs({ options: { out: { type: 'string' } } });
const output = args.out ? pathToFileURL(args.out.replace(/[\\/]*$/, '/')) : new URL('dist/', source);

const manifest = JSON.parse(await readFile(new URL('manifest.json', source), 'utf8'));
if (manifest.manifest_version !== 3) throw new Error('manifest must be MV3');
if (manifest.key || manifest.content_security_policy) {
  throw new Error('manifest must stay generic: no key and no custom CSP');
}
const pyInit = await readFile(new URL('src/gemini_remote/__init__.py', root), 'utf8');
const pyVersion = /__version__ = "([^"]+)"/.exec(pyInit)?.[1];
const pyproject = await readFile(new URL('pyproject.toml', root), 'utf8');
const projectVersion = /^version = "([^"]+)"/m.exec(pyproject)?.[1];
if (pyVersion !== manifest.version || projectVersion !== manifest.version) {
  throw new Error(
    `version mismatch: manifest ${manifest.version}, __init__ ${pyVersion}, pyproject ${projectVersion}`,
  );
}

const files = [
  [new URL('adapters/gemini/selectors.js', root), 'selectors.js'],
  ...['background.js', 'relay-client.js', 'content-entry.js', 'panel.js', 'popup.html', 'popup.js'].map(
    (name) => [new URL(name, source), name],
  ),
];
await rm(new URL('extension-origin.txt', output), { force: true }); // left by builds before 0.1.4
await mkdir(output, { recursive: true });
await writeFile(new URL('manifest.json', output), JSON.stringify(manifest, null, 2) + '\n', 'utf8');
for (const [from, name] of files) await copyFile(from, new URL(name, output));
console.log('Built ' + fileURLToPath(output) + ' (v' + manifest.version + ')');
