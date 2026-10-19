import { readFileSync } from 'node:fs';
import { inlineFrontendModules } from './inline-modules.mjs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const ROOT = process.cwd();
const HTML_PATH = path.join(ROOT, 'static', 'index.html');

function makeJsonResponse(body, status = 200) {
  const payload = structuredClone(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

async function waitFor(fn, timeout = 2000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (fn()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Timed out waiting for condition');
}

const playlists = [
  { id: 3, name: 'Evening', track_count: 12 },
  { id: 5, name: 'Focus', track_count: 2 },
];
const requests = [];
let promptCalls = 0;

const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8')).replace(/<link[^>]+>/g, '');
const dom = new JSDOM(html, {
  url: 'http://localhost/#tracks',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.prompt = () => { promptCalls++; return null; };
    window.confirm = () => true;
    window.alert = () => {};
    window.scrollTo = () => {};
    window.requestAnimationFrame = cb => window.setTimeout(() => cb(Date.now()), 0);
    window.cancelAnimationFrame = id => window.clearTimeout(id);
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
    window.fetch = (url, opts = {}) => {
      const method = (opts.method || 'GET').toUpperCase();
      const parsed = new URL(url, 'http://localhost');
      const body = opts.body ? JSON.parse(opts.body) : null;
      requests.push({ method, path: parsed.pathname, body });
      if (parsed.pathname === '/api/playlists' && method === 'GET') {
        return Promise.resolve(makeJsonResponse(playlists));
      }
      if (parsed.pathname === '/api/playlists' && method === 'POST') {
        const created = { id: 9, name: body.name, track_count: 0 };
        playlists.push(created);
        return Promise.resolve(makeJsonResponse(created, 201));
      }
      if (/^\/api\/playlists\/\d+\/tracks$/.test(parsed.pathname) && method === 'POST') {
        return Promise.resolve(makeJsonResponse({ success: true }));
      }
      if (parsed.pathname === '/api/dsp/profiles' && method === 'GET') {
        return Promise.resolve(makeJsonResponse([]));
      }
      // The page asks for a lot on boot; none of it matters here.
      return Promise.resolve(makeJsonResponse({}));
    };
  },
});

const { document } = dom.window;
const modal = document.querySelector('#modal-container');

await dom.window.showPlaylistPicker({ id: 11, title: 'First <song>' });

// A Home Screen web app on iOS never shows a native dialog: prompt() comes
// back null and the gesture dies quietly. The picker has to be the app's own.
assert.equal(promptCalls, 0, 'The picker must not use a native dialog');
assert.ok(modal.classList.contains('open'), 'The picker opens as a panel');
assert.equal(document.querySelector('#modal-title').textContent, 'Add to playlist');
assert.equal(document.querySelectorAll('.playlist-pick-item').length, 2);
assert.match(document.querySelector('#modal-body').textContent, /Evening/);
assert.match(document.querySelector('#modal-body').textContent, /12 tracks/);
// A title with markup in it must not become markup.
assert.ok(!document.querySelector('#modal-body').innerHTML.includes('First <song>'));

// Tapping a playlist adds the track to it.
document.querySelector('.playlist-pick-item').click();
await waitFor(() => requests.some(r => r.method === 'POST' && r.path === '/api/playlists/3/tracks'));
const add = requests.find(r => r.method === 'POST' && r.path === '/api/playlists/3/tracks');
assert.equal(add.body.track_id, 11);
await waitFor(() => !modal.classList.contains('open'));

// A new playlist is named in the panel, not in a native dialog.
await dom.window.showPlaylistPicker({ id: 11, title: 'First <song>' });
document.querySelector('#modal-confirm').click();
await waitFor(() => document.querySelector('#toast').textContent.length > 0);
assert.ok(modal.classList.contains('open'), 'An empty name keeps the panel open');

document.querySelector('#playlist-new-name').value = 'Late night';
document.querySelector('#modal-confirm').click();
await waitFor(() => requests.some(r => r.method === 'POST' && r.path === '/api/playlists'
  && r.body.name === 'Late night'));
assert.equal(promptCalls, 0, 'Creating a playlist must not use a native dialog either');
await waitFor(() => !modal.classList.contains('open'));

dom.window.close();
console.log('frontend_playlist_picker=OK');
