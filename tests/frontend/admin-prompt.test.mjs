// Real index.html in jsdom: listening never asks for a password; only an
// admin route's 401 opens the admin prompt, and the action is retried with the
// issued key once the password is confirmed.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const HTML_PATH = path.join(process.cwd(), 'static', 'index.html');

function json(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: name => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
    async json() { return structuredClone(body); },
    async text() { return JSON.stringify(body); },
  };
}

async function waitFor(predicate, timeoutMs = 5000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Timed out waiting for condition');
}

const ADMIN_KEY = 'admin-key';
const calls = [];
function route(url, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const { pathname } = new URL(url, 'http://localhost');
  const headers = opts.headers || {};
  calls.push({ method, pathname, headers: { ...headers } });
  if (pathname === '/api/security/login' && method === 'POST') {
    if (headers['X-Password'] === 'right-password') {
      return Promise.resolve(json({ success: true, api_key: ADMIN_KEY, stream_ticket: 'T',
        stream_ticket_expires_at: Math.floor(Date.now() / 1000) + 4 * 3600 }));
    }
    return Promise.resolve(json({ error: 'Invalid password or API key' }, 401));
  }
  if (pathname === '/api/library/reset' && method === 'POST') {
    return Promise.resolve(headers['X-API-Key'] === ADMIN_KEY
      ? json({ success: true })
      : json({ error: 'Authentication required' }, 401));
  }
  if (pathname === '/api/stats') return Promise.resolve(json({ artists: 0, albums: 0, tracks: 0, duration: 0 }));
  if (pathname === '/api/scan/status') return Promise.resolve(json({ running: false }));
  if (pathname === '/api/dsp/profiles') return Promise.resolve(json([]));
  if (pathname === '/api/config') return Promise.resolve(json({ music_dir: 'C:/Music', libraries: [] }));
  if (/\/status$/.test(pathname)) return Promise.resolve(json({ running: false }));
  return Promise.resolve(json([]));
}

const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8')).replace(/<link[^>]+>/g, '');
const dom = new JSDOM(html, {
  url: 'http://localhost/#home',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = route;
    window.confirm = () => true;
    window.alert = () => {};
    window.scrollTo = () => {};
    window.matchMedia = query => ({ media: query, matches: false, addListener() {}, removeListener() {},
      addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
    window.requestAnimationFrame = cb => window.setTimeout(() => cb(Date.now()), 0);
    window.cancelAnimationFrame = id => window.clearTimeout(id);
    window.MediaMetadata = class { constructor(data) { Object.assign(this, data); } };
    window.navigator.mediaSession = { metadata: null, playbackState: 'none', setActionHandler() {} };
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
    window.HTMLMediaElement.prototype.load = () => {};
  },
});
const { window } = dom;
const doc = window.document;
const modal = () => doc.getElementById('login-modal');

// 1. Startup without a saved key: the library renders and nothing prompts.
await waitFor(() => window.eval('state.view') === 'home');
await new Promise(resolve => setTimeout(resolve, 100));
assert.equal(modal(), null, 'Listening must not ask for a password');
assert.ok(!calls.some(c => c.pathname === '/api/security/login'), 'No login at startup without a key');

// 2. An admin action opens the prompt; a wrong password keeps it open with an error.
const first = window.api('/library/reset', { method: 'POST' });
await waitFor(modal);
assert.match(modal().textContent, /Admin Password Required/);
doc.getElementById('login-password').value = 'wrong';
doc.getElementById('login-submit').click();
await waitFor(() => doc.getElementById('login-error')?.textContent);
assert.equal(doc.getElementById('login-error').textContent, 'Incorrect password');
assert.ok(modal(), 'A wrong password keeps the prompt open');

// 3. The right password stores the key and retries the action with it.
doc.getElementById('login-password').value = 'right-password';
doc.getElementById('login-submit').click();
assert.equal((await first)?.success, true);
assert.equal(modal(), null);
assert.equal(window.localStorage.getItem('velvet_api_key'), ADMIN_KEY);
const retried = calls.filter(c => c.pathname === '/api/library/reset');
assert.equal(retried.length, 2);
assert.equal(retried[1].headers['X-API-Key'], ADMIN_KEY);

// 4. Cancelling resolves the action as null without retrying.
window.eval("storeApiKey('')");
const cancelled = window.api('/library/reset', { method: 'POST' });
await waitFor(modal);
doc.getElementById('login-cancel').click();
assert.equal(await cancelled, null);
assert.equal(modal(), null);
assert.equal(calls.filter(c => c.pathname === '/api/library/reset').length, 3);

// 5. silentAuth (startup session refresh with a stale key) never prompts.
assert.equal(await window.api('/library/reset', { method: 'POST', silentAuth: true }), null);
assert.equal(modal(), null);

window.close();
console.log('frontend_admin_prompt_check=OK');
