import * as mdi from '@mdi/js';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const packageRoot = dirname(require.resolve('@mdi/js/package.json'));
const pkg = JSON.parse(await readFile(resolve(packageRoot, 'package.json'), 'utf8'));
if (pkg.version !== '7.4.47') throw new Error('Pinned MDI version changed');
const shards = Array.from({ length: 64 }, () => ({}));
export function shardFor(name) { let hash = 0; for (const char of name) hash = (hash*31+char.charCodeAt(0)) >>> 0; return hash%64; }
const paths = Object.fromEntries(Object.entries(mdi).filter(([key, value]) => /^mdi[A-Z0-9]/.test(key) && typeof value === 'string'));
for (const [key, value] of Object.entries(paths)) shards[shardFor(key)][key] = value;
const target = resolve(root, 'src/assets/mdi');
await mkdir(target, { recursive: true });
for (let index = 0; index < shards.length; index++) await writeFile(resolve(target, `${index.toString(16).padStart(2, '0')}.json`), JSON.stringify(shards[index])+'\n');
const small = Object.fromEntries(['thermometer', 'lightbulb', 'account-check', 'sleep', 'play-circle', 'shape', 'home', 'window-open-variant', 'weather-sunset'].map(name => [`mdi:${name}`, paths['mdi'+name.split('-').map(part => part[0].toUpperCase()+part.slice(1)).join('')]]));
await writeFile(resolve(root, 'src/assets/mdi-categories.json'), JSON.stringify(small)+'\n');
await mkdir(resolve(root, 'public/licenses'), { recursive: true });
await writeFile(resolve(root, 'public/licenses/material-design-icons.txt'), await readFile(resolve(packageRoot, 'LICENSE'), 'utf8'));
const sizes = shards.map(part => Buffer.byteLength(JSON.stringify(part)));
const report = { version: pkg.version, license: pkg.license, paths: Object.keys(paths).length, shards: shards.length, total_bytes: sizes.reduce((a,b) => a+b, 0), largest_shard_bytes: Math.max(...sizes), initial_paths_bytes: Buffer.byteLength(JSON.stringify(small)) };
await writeFile(resolve(target, 'manifest.json'), JSON.stringify(report, null, 2)+'\n');
console.log(JSON.stringify(report));
