import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const listeners = new Map();
const stores = new Map();
const key = request => new URL(typeof request === 'string' ? request : request.url, 'http://localhost').href;
const caches = {
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const entries = stores.get(name);
    return {
      async put(request, response) { entries.set(key(request), response); },
      async match(request) { return entries.get(key(request)); },
      async keys() { return [...entries.keys()]; },
      async delete(request) { return entries.delete(key(request)); },
    };
  },
  async keys() { return [...stores.keys()]; },
  async delete(name) { return stores.delete(name); },
  async match(request) {
    for (const entries of stores.values()) if (entries.has(key(request))) return entries.get(key(request));
  },
};
let cacheControl = '';
let networkCalls = 0;
function response() {
  const r = new Response('asset', { headers: { 'Cache-Control': cacheControl } });
  Object.defineProperty(r, 'type', { value: 'basic' });
  const clone = r.clone.bind(r);
  r.clone = () => {
    const c = clone();
    Object.defineProperty(c, 'type', { value: 'basic' });
    return c;
  };
  return r;
}
const context = vm.createContext({ URL, Response, caches,
  fetch: async () => { networkCalls++; return response(); },
  self: { location: { origin: 'http://localhost' }, skipWaiting() {}, clients: { claim() {} },
    addEventListener(name, callback) { listeners.set(name, callback); } },
});
vm.runInContext(readFileSync('sw.js', 'utf8'), context);
async function dispatch(type, request) {
  const pending = [];
  let result;
  listeners.get(type)({ request, waitUntil(p) { pending.push(p); }, respondWith(p) { result = p; } });
  const value = await result;
  await Promise.all(pending);
  return value;
}
function request(path, method = 'GET') { return { url: key(path), method }; }

await dispatch('install');
assert.ok(await caches.match('/static/index.html'));
assert.equal(networkCalls, 10, 'Install fetches only existing cacheable public assets');
cacheControl = 'private, NO-STORE';
await dispatch('fetch', request('/'));
assert.equal(await caches.match('/'), undefined, 'Root no-store must be respected');
await dispatch('fetch', request('/api/covers/1/thumb'));
assert.equal(await caches.match('/api/covers/1/thumb'), undefined);
await dispatch('fetch', request('/api/covers/1'));
assert.equal(await caches.match('/api/covers/1'), undefined);
cacheControl = '';
await dispatch('fetch', request('/api/security/status'));
await dispatch('fetch', request('/api/stream/1?api_key=secret'));
assert.equal(await caches.match('/api/security/status'), undefined);
assert.equal(await caches.match('/api/stream/1?api_key=secret'), undefined);
await dispatch('fetch', request('/api/covers/1?api_key=secret'));
assert.equal(await caches.match('/api/covers/1?api_key=secret'), undefined);
for (let i = 0; i < 410; i++) await dispatch('fetch', request(`/api/covers/${i}/thumb`));
assert.equal(stores.get('velvet-thumbs-v5').size, 400);
const before = networkCalls;
await dispatch('fetch', request('/api/covers/409/thumb'));
assert.equal(networkCalls, before, 'Cached thumbnail avoids a new request');
for (let i = 0; i < 90; i++) await dispatch('fetch', request(`/api/covers/${i}`));
assert.equal(stores.get('velvet-covers-v3').size, 80);
await dispatch('fetch', request('https://external.example/image'));
await dispatch('fetch', request('/api/covers/1', 'POST'));
assert.equal(networkCalls, before + 90, 'Cross-origin and mutation requests are untouched');

const html = readFileSync('static/index.html', 'utf8');
const loginSource = html.slice(html.indexOf('async function submitLogin()'), html.indexOf('function coverUrl('));
let rendered = 0;
const requests = [];
const authState = {};
const loginContext = vm.createContext({ authState,
  document: { getElementById(id) { return id === 'login-password' ? { value: 'password' } : { remove() {} }; } },
  localStorage: { setItem() {} },
  alert(message) { throw new Error(message); },
  async render() { rendered++; },
  async loadDspProfiles() {},
  adoptStreamTicket() {},
  async fetch(url, options) {
    requests.push({ url, options });
    return { ok: true, async json() { return { success: true, api_key: 'shared-key' }; } };
  },
});
vm.runInContext(loginSource, loginContext);
await vm.runInContext('submitLogin()', loginContext);
assert.equal(authState.apiKey, 'shared-key');
assert.equal(rendered, 1);
assert.equal(requests[1].url, '/api/security/login', 'Login must not rotate other clients\' keys');
assert.equal(requests[1].options.headers['X-Password'], 'password');

// Credential changes purge the authenticated cover caches the SW serves
// before the network; the static app shell cache is kept.
const remoteSettings = readFileSync('static/js/remote-settings.js', 'utf8');
const settingsSource = remoteSettings.slice(remoteSettings.indexOf('async function savePassword()'),
  remoteSettings.indexOf('// Auto-refresh remote status in background'));
const stored = new Map([['velvet_api_key', 'old-key']]);
const order = [];
let loginOk = true;
const settingsAuth = { apiKey: 'old-key' };
const settingsContext = vm.createContext({ authState: settingsAuth, caches, state: {},
  document: { getElementById(id) { return id === 'new-password' ? { value: 'new-password' } : {}; } },
  localStorage: { setItem(k, v) { stored.set(k, v); }, removeItem(k) { stored.delete(k); } },
  alert() {},
  async render() {},
  adoptStreamTicket() { order.push(['ticket', settingsAuth.apiKey]); },
  async api(path) {
    return path === '/security/generate-api-key'
      ? { success: true, api_key: 'rotated-key' }
      : { success: true, message: 'Password set successfully' };
  },
  async fetch() {
    return { ok: loginOk, async json() { return { success: true, api_key: 'rotated-key' }; } };
  },
});
vm.runInContext(loginSource, settingsContext);
vm.runInContext(settingsSource, settingsContext);
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const coverCaches = () => [...stores.keys()].filter(name => /^velvet-(thumbs|covers)-v/.test(name));
async function seedCoverCaches() {
  await dispatch('fetch', request('/api/covers/1/thumb'));
  await dispatch('fetch', request('/api/covers/1'));
  assert.deepEqual(coverCaches().sort(), ['velvet-covers-v3', 'velvet-thumbs-v5']);
}

assert.equal(coverCaches().length, 2, 'SW tests above populated both cover caches');
await vm.runInContext('generateNewApiKey()', settingsContext);
await settle();
assert.deepEqual(coverCaches(), [], 'API-key rotation purges thumbnail and cover caches');
const shellCache = readFileSync('sw.js', 'utf8').match(/const CACHE_NAME = '([^']+)'/)[1];
assert.ok(stores.has(shellCache), 'The app shell cache is not purged');
assert.equal(stored.get('velvet_api_key'), 'rotated-key');
assert.deepEqual(order, [['ticket', 'rotated-key']], 'The stream ticket is adopted for the new key');

for (const ok of [true, false]) {
  await seedCoverCaches();
  loginOk = ok;
  await vm.runInContext('savePassword()', settingsContext);
  await settle();
  assert.deepEqual(coverCaches(), [], `Password change purges cover caches (key fetch ok=${ok})`);
}

await seedCoverCaches();
await vm.runInContext("storeApiKey('')", settingsContext);
assert.deepEqual(coverCaches(), [], 'Clearing the API key purges cover caches');
assert.equal(stored.has('velvet_api_key'), false);

const beforeOffline = networkCalls;
await dispatch('fetch', request('/api/covers/7/thumb'));
await dispatch('fetch', request('/api/covers/7/thumb'));
assert.equal(networkCalls, beforeOffline + 1, 'Thumbnail caching works again after a purge');
assert.equal(stores.get('velvet-thumbs-v5').size, 1);
console.log('frontend_pwa_auth_check=OK');
