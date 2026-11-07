import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

// Playlist page with a fake window.VelvetOffline: one added button, small
// row markers, and pages that still open from the saved manifest offline.
const windows = [];
function setup({ offline, apiImpl } = {}) {
  const dom = new JSDOM('<main id="main"><div id="view-container"></div></main>', {
    url: 'http://localhost/#playlist/1', runScripts: 'outside-only',
  });
  const { window } = dom;
  windows.push(window);
  const seen = { toasts: [], confirmation: null, navigations: [] };
  Object.assign(window, {
    UI: new Proxy({}, { get: (_, id) => window.document.getElementById(id) }),
    escHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char])),
    fmt: () => '3:00', fmtSR: () => '', playTrack() {},
    showToast: message => seen.toasts.push(message),
    showConfirm: options => { seen.confirmation = options; },
    navigate: (...args) => seen.navigations.push(args),
    getTopNavHtml: () => '',
    api: apiImpl || (async path => (path === '/playlists'
      ? [{ id: 1, name: 'Mix', track_count: 2 }]
      : { id: 1, name: 'Mix', description: '', tracks: [{ id: 11, title: 'A' }, { id: 22, title: 'B' }] })),
    VelvetOffline: offline,
  });
  window.eval(readFileSync('static/js/library-cards.js', 'utf8'));
  window.eval(readFileSync('static/js/library-views.js', 'utf8'));
  return { window, view: window.document.getElementById('view-container'), seen };
}

function fakeOffline({ stored = [] } = {}) {
  const listeners = new Set();
  const fake = {
    stored: new Set(stored), job: null, downloads: [], removed: [], paused: 0,
    manifests: new Map(), bytes: 0,
    has: id => fake.stored.has(Number(id)),
    status(playlist) {
      const total = playlist.tracks.length;
      const downloaded = playlist.tracks.filter(t => fake.has(t.id)).length;
      return { total, downloaded, missing: total - downloaded };
    },
    job: () => fake.currentJob || null,
    pause() { fake.paused++; },
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    emit(job) { fake.currentJob = job; listeners.forEach(fn => fn(job)); },
    listenerCount: () => listeners.size,
    async download(playlist) { fake.downloads.push(playlist); if (fake.downloadError) throw fake.downloadError; return fake.result || { downloaded: 0, failed: 0, stopped: null }; },
    async remove(id) { fake.removed.push(id); fake.stored.clear(); },
    async getPlaylist(id) { return fake.manifests.get(Number(id)) || null; },
    async listPlaylists() { return [...fake.manifests.values()]; },
    async usage() { return fake.bytes; },
  };
  return fake;
}

const button = view => view.querySelector('#offline-playlist-btn');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const waitUntil = async (predicate, ms = 2000) => { const end = Date.now() + ms; while (!predicate()) { if (Date.now() > end) throw new Error("Timed out waiting for condition"); await settle(); } };

// Without the offline module the page is exactly as before.
{
  const { window, view } = setup({ offline: undefined });
  await window.renderPlaylist(view, { id: 1 });
  assert.equal(button(view), null);
  assert.equal(view.querySelectorAll('.playlist-toolbar-actions .btn').length, 2);
}

// Nothing downloaded: "Download", placed with Rename/Delete in the same style.
{
  const offline = fakeOffline();
  const { window, view } = setup({ offline });
  await window.renderPlaylist(view, { id: 1 });
  const btn = button(view);
  assert.equal(btn.textContent.trim(), 'Download');
  assert.equal(btn.className, 'btn btn-ghost');
  assert.equal(btn.parentElement.className, 'playlist-toolbar-actions');
  assert.equal(btn.nextElementSibling.id, 'rename-playlist-btn');
  btn.click();
  await settle();
  assert.equal(offline.downloads.length, 1);
  assert.equal(offline.downloads[0].id, 1);
  assert.equal(offline.downloads[0].name, 'Mix');
  assert.equal(offline.downloads[0].tracks.length, 2);

  // Progress for this playlist shows as done/total; a tap pauses.
  offline.emit({ playlistId: 1, done: 1, total: 2 });
  assert.equal(btn.textContent.trim(), '1/2');
  btn.click();
  assert.equal(offline.paused, 1);
  assert.equal(offline.downloads.length, 1, 'Pausing never starts another download');

  // Leaving the page unsubscribes on the next change.
  view.innerHTML = '';
  offline.emit({ playlistId: 1, done: 2, total: 2 });
  assert.equal(offline.listenerCount(), 0);
}

// Downloaded tracks get a marker; partial downloads offer the missing count.
{
  const offline = fakeOffline({ stored: [11] });
  const { window, view } = setup({ offline });
  await window.renderPlaylist(view, { id: 1 });
  const rows = view.querySelectorAll('.track-item');
  assert.ok(rows[0].classList.contains('is-offline'));
  assert.ok(!rows[1].classList.contains('is-offline'));
  assert.equal(button(view).textContent.trim(), '+1');
  offline.stored.add(22);
  offline.emit(null);
  assert.ok(view.querySelectorAll('.track-item')[1].classList.contains('is-offline'), 'Markers follow progress');
  assert.equal(button(view).textContent.trim(), 'Saved');
}

// Fully downloaded: the button removes the offline copy after confirmation.
{
  const offline = fakeOffline({ stored: [11, 22] });
  const { window, view, seen } = setup({ offline });
  await window.renderPlaylist(view, { id: 1 });
  assert.equal(button(view).textContent.trim(), 'Saved');
  button(view).click();
  assert.equal(seen.confirmation.title, 'Remove offline copy?');
  assert.equal(seen.confirmation.confirmText, 'Remove');
  assert.deepEqual(offline.removed, []);
  await seen.confirmation.onConfirm();
  assert.deepEqual(offline.removed, [1]);
  assert.equal(button(view).textContent.trim(), 'Download');
  assert.equal(view.querySelectorAll('.track-item.is-offline').length, 0);
}

// Download results are reported.
{
  const offline = fakeOffline();
  offline.result = { downloaded: 1, failed: 0, stopped: 'quota' };
  const { window, view, seen } = setup({ offline });
  await window.renderPlaylist(view, { id: 1 });
  button(view).click();
  await settle();
  assert.ok(seen.toasts.includes('Not enough storage on this device'));
  offline.result = { downloaded: 0, failed: 2, stopped: null };
  button(view).click();
  await settle();
  assert.ok(seen.toasts.includes('2 tracks could not be downloaded'));
  offline.result = { downloaded: 0, failed: 0, stopped: 'busy' };
  button(view).click();
  await settle();
  assert.ok(seen.toasts.includes('Another playlist is downloading'));
  offline.downloadError = new Error('IndexedDB unavailable');
  button(view).click();
  await settle();
  assert.ok(seen.toasts.includes('Download could not start'), seen.toasts.join(' | '));
  assert.equal(button(view).textContent.trim(), 'Download', 'The button recovers');
}

// Server unreachable: the playlist opens from its saved manifest.
{
  const offline = fakeOffline({ stored: [11] });
  offline.manifests.set(1, { id: 1, name: 'Saved mix', tracks: [{ id: 11, title: 'Saved A' }], trackIds: [11] });
  const { window, view } = setup({ offline, apiImpl: async () => null });
  await window.renderPlaylist(view, { id: '1' });
  assert.ok(view.textContent.includes('Saved mix'));
  assert.equal(view.querySelectorAll('.track-item').length, 1);
  assert.ok(view.querySelector('.track-item').classList.contains('is-offline'));
}

// Playlists page: offline manifests stand in for the server list, and the
// space used is added to the subtitle.
{
  const offline = fakeOffline();
  offline.manifests.set(1, { id: 1, name: 'Saved mix', tracks: [{ id: 11 }], trackIds: [11] });
  offline.bytes = 1.2e9;
  const { window, view } = setup({ offline, apiImpl: async () => null });
  await window.renderPlaylists(view);
  await settle();
  assert.equal(view.querySelectorAll('#playlists-grid .card').length, 1);
  assert.ok(view.textContent.includes('Saved mix'));
  assert.ok(view.textContent.includes('1 tracks'));
  assert.match(view.querySelector('.section-sub').textContent, /1 playlist · 1\.2 GB offline/);
}
{
  const offline = fakeOffline();
  offline.bytes = 0;
  const { window, view } = setup({ offline });
  await window.renderPlaylists(view);
  await settle();
  assert.equal(view.querySelector('.section-sub').textContent, '1 playlist', 'No usage, no suffix');
}

// Deleting a playlist on the server also frees its offline copy.
{
  const offline = fakeOffline({ stored: [11, 22] });
  const { window, view } = setup({ offline, apiImpl: async (path, options = {}) => (options.method === 'DELETE'
    ? { success: true }
    : { id: 1, name: 'Mix', tracks: [{ id: 11, title: 'A' }, { id: 22, title: 'B' }] }) });
  window.confirm = () => true;
  await window.renderPlaylist(view, { id: 1 });
  view.querySelector('#delete-playlist-btn').click();
  await waitUntil(() => offline.removed.length === 1);
  assert.deepEqual(offline.removed, [1]);
}

// Online, offline copies of playlists the server no longer has are removed.
{
  const offline = fakeOffline();
  offline.manifests.set(1, { id: 1, name: 'Mix', tracks: [], trackIds: [] });
  offline.manifests.set(9, { id: 9, name: 'Gone', tracks: [], trackIds: [] });
  const { window, view } = setup({ offline });
  await window.renderPlaylists(view);
  await waitUntil(() => offline.removed.length === 1);
  assert.deepEqual(offline.removed, [9]);
}

windows.forEach(window => window.close());
console.log('frontend_offline_playlist_ui=OK');
