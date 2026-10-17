import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const listeners = new Map(), stores = new Map();
const key = r => new URL(typeof r === 'string' ? r : r.url, 'http://localhost').href;
let calls = 0, failAt = 0, activated = 0, offline = false, body = 'installed-release';
const cacheApi = {
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const entries = stores.get(name);
    return { async put(r, response) { entries.set(key(r), response); }, async match(r) { return entries.get(key(r))?.clone(); }, async keys() { return [...entries.keys()]; } };
  },
  async keys() { return [...stores.keys()]; }, async delete(n) { return stores.delete(n); },
  async match(r) { for (const entries of stores.values()) if (entries.has(key(r))) return entries.get(key(r)).clone(); },
};
const sandbox = vm.createContext({ URL, Response, caches: cacheApi,
  async fetch() {
    if (offline || ++calls === failAt) throw new Error('fixture network failure');
    const response = new Response(body); Object.defineProperty(response, 'type', { value: 'basic' }); return response;
  },
  self: { location: { origin: 'http://localhost' }, skipWaiting() { activated++; }, clients: { claim() {} }, addEventListener(n, fn) { listeners.set(n, fn); } },
});
vm.runInContext(readFileSync('sw.js', 'utf8'), sandbox);
async function dispatch(type, args = {}) {
  const pending = []; let response;
  listeners.get(type)({ ...args, waitUntil(p) { pending.push(p); }, respondWith(p) { response = p; } });
  const result = await response; await Promise.all(pending); return result;
}
failAt = 4;
await assert.rejects(dispatch('install'), /network failure/);
assert.equal(stores.size, 0, 'a partial download must never become an installed shell');
failAt = 0; calls = 0;
await dispatch('install');
assert.equal(calls, 10);
assert.equal(activated, 0, 'installation waits instead of taking over an active player');
const html = readFileSync('static/index.html', 'utf8');
for (const name of ['library-views', 'player-ui', 'library-cards', 'dsp-controls']) {
  assert.ok(html.includes(`/static/js/${name}.js?v=mobile-20261017-1`));
}
const before = calls;
const request = p => ({ url: key(p), method: 'GET', mode: 'same-origin' });
const cached = await dispatch('fetch', { request: request('/static/js/settings-view.js?v=mobile-20261017-1') });
assert.equal(await cached.text(), 'installed-release');
assert.equal(calls, before, 'lazy modules use the same installed release');
body = 'newer-online-html';
await dispatch('fetch', { request: request('/static/index.html') });
assert.equal(await (await cacheApi.match('/static/index.html')).text(), 'installed-release', 'online HTML does not overwrite the coherent offline shell');
offline = true;
const fallback = await dispatch('fetch', { request: { ...request('/'), mode: 'navigate' } });
assert.equal(await fallback.text(), 'installed-release');
await dispatch('message', { data: { type: 'irrelevant' } }); assert.equal(activated, 0);
await dispatch('message', { data: { type: 'ACTIVATE_UPDATE' } }); assert.equal(activated, 1);
console.log('frontend_pwa_release=OK (14 checks)');
