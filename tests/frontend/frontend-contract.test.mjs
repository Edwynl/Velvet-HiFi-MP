// Frontend contract guards for the two ways a control silently dies:
//
// 1. UI['id'] lookups. UI is a proxy that resolves through getElementById,
//    so a template that only sets a class leaves the lookup null — the
//    optional chaining then skips the listener and the control is dead.
// 2. Inline onclick="fn(...)" handlers whose function does not exist.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const jsDir = join(root, 'static', 'js');
const files = [
  join(root, 'static', 'index.html'),
  ...readdirSync(jsDir).filter(f => f.endsWith('.js')).map(f => join(jsDir, f)),
];
const sources = files.map(file => ({ file: file.slice(root.length), text: readFileSync(file, 'utf8') }));

// ── 1. UI lookups must resolve to a real id ───────────────────────────────────
const lookups = new Map();
for (const { file, text } of sources) {
  for (const match of text.matchAll(/UI\[['"]([\w-]+)['"]\]/g)) {
    const line = text.slice(0, match.index).split('\n').length;
    if (!lookups.has(match[1])) lookups.set(match[1], []);
    lookups.get(match[1]).push(`${file}:${line}`);
  }
}

const definedIds = new Set();
for (const { text } of sources) {
  for (const match of text.matchAll(/\bid=["']([\w-]+)["']/g)) definedIds.add(match[1]);
}

const orphanLookups = [...lookups.entries()]
  .filter(([key]) => !definedIds.has(key))
  .map(([key, places]) => `${key} (${places.join(', ')})`);

assert.equal(
  orphanLookups.length, 0,
  `UI lookups without a matching id in the templates: ${orphanLookups.join('; ')}`
);

// ── 2. Inline handlers must name a function that exists ──────────────────────
const definedFunctions = new Set();
for (const { text } of sources) {
  for (const m of text.matchAll(/(?:^|\n)\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/g)) definedFunctions.add(m[1]);
  for (const m of text.matchAll(/(?:^|\n)\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function)/g)) definedFunctions.add(m[1]);
  for (const m of text.matchAll(/window\.([A-Za-z_$][\w$]*)\s*=/g)) definedFunctions.add(m[1]);
}

const orphanHandlers = [];
for (const { file, text } of sources) {
  for (const m of text.matchAll(/\bon(?:click|change|input|error|load)="\s*([A-Za-z_$][\w$]*)\s*\(/g)) {
    if (definedFunctions.has(m[1])) continue;
    const line = text.slice(0, m.index).split('\n').length;
    orphanHandlers.push(`${file}:${line} → ${m[1]}()`);
  }
}

assert.equal(
  orphanHandlers.length, 0,
  `inline handlers calling an undefined function: ${orphanHandlers.join('; ')}`
);

console.log(`frontend_contract=OK (${lookups.size} UI lookups resolved by id, inline handlers defined)`);
