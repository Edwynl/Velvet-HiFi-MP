import { readFileSync } from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const HTML_PATH = path.join(process.cwd(), 'static', 'index.html');

function makeJsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return structuredClone(body); },
    async text() { return JSON.stringify(body); },
  };
}

async function waitFor(predicate, timeoutMs = 4000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Timed out waiting for settings UI');
}

const libraries = [{ id: 1, path: 'C:\\Users\\edwyn\\Music' }];
let nextLibraryId = 2;
let lastScanFolders = null;
let scanStatus = {
  running: false,
  progress: 0,
  total: 0,
  current_file: '',
  tracks_added: 0,
  tracks_skipped: 0,
  albums_added: 0,
  library_albums: 12,
  library_tracks: 120,
  library_artists: 8,
  finished_at: null,
};

function route(url, opts = {}) {
  const parsed = new URL(url, 'http://localhost');
  const method = (opts.method || 'GET').toUpperCase();

  if (parsed.pathname === '/api/stats') {
    return Promise.resolve(makeJsonResponse({ artists: 8, albums: 12, tracks: 120, duration: 0 }));
  }
  if (parsed.pathname === '/api/dsp/profiles') return Promise.resolve(makeJsonResponse([]));
  if (parsed.pathname === '/api/scan/status') return Promise.resolve(makeJsonResponse(scanStatus));
  if (parsed.pathname === '/api/enrich/fingerprint/status') {
    return Promise.resolve(makeJsonResponse({ running: false, processed: 0, total: 0, matched: 0 }));
  }
  if (parsed.pathname === '/api/enrich/bios/status') {
    return Promise.resolve(makeJsonResponse({ running: false, processed: 0, total: 0, fetched: 0, not_found: 0 }));
  }
  if (parsed.pathname === '/api/config' && method === 'GET') {
    return Promise.resolve(makeJsonResponse({ libraries, music_dir: libraries[0].path, port: 8765, data_dir: 'data' }));
  }
  if (parsed.pathname === '/api/config/libraries' && method === 'POST') {
    const { path: libraryPath } = JSON.parse(opts.body);
    const exists = libraries.some(item => item.path === libraryPath);
    if (!exists) libraries.push({ id: nextLibraryId++, path: libraryPath });
    return Promise.resolve(makeJsonResponse({ libraries, added: !exists, path: libraryPath }));
  }
  if (parsed.pathname === '/api/scan' && method === 'POST') {
    const body = JSON.parse(opts.body || '{}');
    lastScanFolders = body.folders || null;
    scanStatus = {
      ...scanStatus,
      running: true,
      progress: 5,
      total: 10,
      current_file: 'disc-01.flac',
      tracks_added: 5,
      albums_added: 2,
      music_dir: lastScanFolders?.join(', ') || 'All configured folders',
    };
    return Promise.resolve(makeJsonResponse({ message: 'Scan started', libraries: lastScanFolders, state: scanStatus }));
  }

  return Promise.resolve(makeJsonResponse({}));
}

const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8')).replace(/<link[^>]+>/g, '');
const dom = new JSDOM(html, {
  url: 'http://localhost/#settings',
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = route;
    window.confirm = () => true;
    window.alert = () => {};
    window.scrollTo = () => {};
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 800 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 1131 });
    window.matchMedia = query => ({
      media: query,
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() { return false; },
    });
    window.requestAnimationFrame = callback => window.setTimeout(() => callback(Date.now()), 0);
    window.cancelAnimationFrame = id => window.clearTimeout(id);
    window.navigator.mediaSession = { setActionHandler() {} };
  },
});

const { document, KeyboardEvent } = dom.window;

await waitFor(() => document.getElementById('add-lib-btn'));

const input = document.getElementById('music-dir-input');
input.value = 'E:\\Roon2';
input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
document.getElementById('add-lib-btn').click();
await waitFor(() => document.querySelectorAll('.library-item').length === 2);

assert.equal(input.value, '', 'input should clear after a successful add');
assert.equal(document.getElementById('library-count').textContent, '2 folders');
assert.match(document.getElementById('library-add-feedback').textContent, /E:\\Roon2 added\. 2 folders configured\./);

input.value = 'F:\\Classical';
input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
assert.match(document.getElementById('library-add-feedback').textContent, /Ready to add this folder/);
document.getElementById('library-add-form').requestSubmit();
await waitFor(() => document.querySelectorAll('.library-item').length === 3);
assert.equal(document.getElementById('library-count').textContent, '3 folders');

document.querySelectorAll('.scan-lib')[1].click();
await waitFor(() => document.querySelector('.scan-status-title')?.textContent.includes('Importing'));

assert.deepEqual(lastScanFolders, ['E:\\Roon2']);
assert.match(document.getElementById('scan-status-panel').textContent, /New albums/);
assert.match(document.getElementById('scan-status-panel').textContent, /disc-01\.flac/);

dom.window.close();
console.log('frontend_settings_library_import=OK');
