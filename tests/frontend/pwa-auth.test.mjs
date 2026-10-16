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
assert.equal(networkCalls, 3, 'Install fetches only existing cacheable public assets');
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
assert.equal(stores.get('velvet-thumbs-v4').size, 400);
const before = networkCalls;
await dispatch('fetch', request('/api/covers/409/thumb'));
assert.equal(networkCalls, before, 'Cached thumbnail avoids a new request');
for (let i = 0; i < 90; i++) await dispatch('fetch', request(`/api/covers/${i}`));
assert.equal(stores.get('velvet-covers-v2').size, 80);
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
console.log('frontend_pwa_auth_check=OK');
