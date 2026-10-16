import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const listeners = new Map();
const stores = new Map();
const urlFor = request => new URL(typeof request === 'string' ? request : request.url, 'http://localhost').href;
const caches = {
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const entries = stores.get(name);
    return {
      async put(req, response) { entries.set(urlFor(req), response); },
      async match(req) { return entries.get(urlFor(req)); },
      async keys() { return [...entries.keys()]; },
      async delete(req) { return entries.delete(urlFor(req)); },
    };
  },
  async keys() { return [...stores.keys()]; },
  async delete(name) { return stores.delete(name); },
};
let body = '12345678';
let cacheControl = '';
const response = () => {
  const r = new Response(body, { headers: { 'Cache-Control': cacheControl } });
  Object.defineProperty(r, 'type', { value: 'basic' });
  const clone = r.clone.bind(r);
  r.clone = () => {
    const x = clone();
    Object.defineProperty(x, 'type', { value: 'basic' });
    return x;
  };
  return r;
};
const sandbox = vm.createContext({
  URL, Response, caches, fetch: async () => response(),
  self: { location: { origin: 'http://localhost' }, skipWaiting() {},
    clients: { claim() {} },
    addEventListener(type, callback) { listeners.set(type, callback); },
  },
});
// Use tiny budgets to test byte eviction without allocating megabytes.
const script = readFileSync('sw.js', 'utf8')
  .replace('24 * 1024 * 1024', '12')
  .replace('40 * 1024 * 1024', '15');
vm.runInContext(script, sandbox);
async function fetchPath(path) {
  let result;
  const wait = [];
  listeners.get('fetch')({
    request: { url: urlFor(path), method: 'GET' },
    respondWith(promise) { result = promise; },
    waitUntil(promise) { wait.push(promise); },
  });
  if (result) await result;
  await Promise.all(wait);
}
const has = (name, path) => stores.get(name)?.has(urlFor(path)) || false;

await fetchPath('/api/covers/1/thumb');
assert.equal(has('velvet-thumbs-v6', '/api/covers/1/thumb'), true);
await fetchPath('/api/covers/2/thumb');
assert.equal(has('velvet-thumbs-v6', '/api/covers/1/thumb'), false, 'Old thumbnail evicted by 12B budget');
assert.equal(has('velvet-thumbs-v6', '/api/covers/2/thumb'), true);

await fetchPath('/api/covers/3?ticket=short-lived-secret');
assert.equal(has('velvet-covers-v4', '/api/covers/3?ticket=short-lived-secret'), false,
  'Scoped bearer-ticket covers must never be persisted');
body = '12345678901234567890';
await fetchPath('/api/covers/4/thumb');
assert.equal(has('velvet-thumbs-v6', '/api/covers/4/thumb'), false, 'Oversize cover is not cached');
body = '12345678';
await fetchPath('/api/covers/4');
await fetchPath('/api/covers/5');
assert.equal(has('velvet-covers-v4', '/api/covers/4'), false, 'Full-cover budget removes oldest');
assert.equal(has('velvet-covers-v4', '/api/covers/5'), true);

// A real browser takes a reader on the response body as soon as respondWith()
// settles, so the cache copy must be cloned before the response is handed over.
{
  let result;
  const wait = [];
  listeners.get('fetch')({
    request: { url: urlFor('/api/covers/7'), method: 'GET' },
    respondWith(promise) { result = promise.then(res => { res.body.getReader(); return res; }); },
    waitUntil(promise) { wait.push(promise); },
  });
  await result;
  await Promise.all(wait);
  assert.equal(has('velvet-covers-v4', '/api/covers/7'), true,
    'Full cover is cached even though the page consumes the response body');
}

cacheControl = 'private, no-store';
await fetchPath('/api/covers/6/thumb');
assert.equal(has('velvet-thumbs-v6', '/api/covers/6/thumb'), false);
console.log('frontend_pwa_byte_budget_check=OK');
