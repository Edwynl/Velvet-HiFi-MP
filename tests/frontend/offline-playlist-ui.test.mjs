import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

// Playlist page with a fake window.VelvetOffline: one added button, small
// row markers, and pages that still open from the saved manifest offline.
const windows = [];
function setup({ offline, apiImpl } = {}) {
  const dom = new JSDOM('<main id="main"><div id="view-container"></div></main><div id="modal-extra"></div>', {
    url: 'http://localhost/#playlist/1', runScripts: 'outside-only',
  });
  const { window } = dom;
  windows.push(window);
  const seen = { toasts: [], confirmation: null, navigations: [], closed: 0 };
  Object.assign(window, {
    UI: new Proxy({}, { get: (_, id) => window.document.getElementById(id) }),
    escHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char])),
    fmt: () => '3:00', fmtSR: () => '', playTrack() {},
    showToast: message => seen.toasts.push(message),
    showConfirm: options => {
      seen.confirmation = options;
      window.document.getElementById('modal-extra').innerHTML = options.extra || '';
      return () => { seen.closed++; };
    },
    navigate: (...args) => seen.navigations.push(args),
    getTopNavHtml: () => '',
    api: apiImpl || (async path => (path === '/playlists'
      ? [{ id: 1, name: 'Mix', track_count: 2 }]
      : { id: 1, name: 'Mix', description: '', tracks: [{ id: 11, title: 'A' }, { id: 22, title: 'B' }] })),
    VelvetOffline: offline,
    // Album page helpers that live in index.html.
    _getCachedData: () => null, _cacheData() {}, isCompactLayout: () => true, isShortLandscapeLayout: () => false,
    $$: selector => [...window.document.querySelectorAll(selector)], state: {}, highlightPlaying() {},
    startRadio() {}, showPlaylistPicker() {}, shuffleArray: list => list, makeWorkId: () => '', makeCreditId: () => '',
    genreFamilyOf: () => ({ id: 'x' }), managementFetch: async () => ({}), loadStats() {}, getThumbSize: () => 'small',
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
    async download(playlist) {
      fake.downloads.push(playlist);
      if (fake.downloadError) throw fake.downloadError;
      if (fake.hang) await new Promise(() => {});
      return fake.result || { downloaded: 0, failed: 0, stopped: null };
    },
    async remove(id) { fake.removed.push(id); fake.stored.clear(); },
    async getPlaylist(id) { return fake.manifests.get(id) || fake.manifests.get(Number(id)) || null; },
    async listPlaylists() { return [...fake.manifests.values()].filter(m => typeof m.id === 'number'); },
    async listAlbums() { return [...fake.manifests.values()].filter(m => typeof m.id === 'string'); },
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
  assert.ok(seen.toasts.includes('Another download is in progress'));
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

// Partly downloaded ("+N"): a tap offers to continue or to delete what is saved.
{
  const offline = fakeOffline({ stored: [11] });
  const { window, view, seen } = setup({ offline });
  await window.renderPlaylist(view, { id: 1 });
  assert.equal(button(view).textContent.trim(), '+1');
  button(view).click();
  assert.equal(seen.confirmation.confirmText, 'Continue download');
  assert.equal(offline.downloads.length, 0, 'Nothing starts before a choice');
  offline.hang = true;
  const closes = await Promise.race([
    Promise.resolve(seen.confirmation.onConfirm()).then(() => 'closed'),
    new Promise(resolve => setTimeout(() => resolve('stuck'), 50)),
  ]);
  assert.equal(closes, 'closed', 'The dialog closes without waiting for the whole download');
  assert.equal(offline.downloads.length, 1);
  offline.hang = false;

  button(view).click();
  const del = window.document.getElementById('offline-delete-partial');
  assert.equal(del.textContent.trim(), 'Delete downloaded');
  del.click();
  await waitUntil(() => offline.removed.length === 1);
  await settle();
  assert.deepEqual(offline.removed, [1]);
  assert.equal(seen.closed, 1, 'Choosing delete closes the dialog');
  assert.equal(button(view).textContent.trim(), 'Download');
}

// Album page: the download lives in the Manage menu; the four buttons stay.
const albumApi = async path => {
  if (path === '/albums/7') {
    return { id: 7, title: 'LP', artist_name: 'Band', artist_id: 3, year: 1999, is_hires: false,
      tracks: [{ id: 11, title: 'A', duration: 60 }, { id: 22, title: 'B', duration: 60 }] };
  }
  if (path === '/albums/7/credits') return {};
  return null;
};
const albumItem = view => view.querySelector('#offline-album-btn');
{
  const { window, view } = setup({ offline: undefined, apiImpl: albumApi });
  await window.renderAlbum(view, { id: 7 });
  assert.equal(albumItem(view), null);
  assert.equal(view.querySelectorAll('.album-actions > .btn').length, 3);
}
{
  const offline = fakeOffline();
  const { window, view, seen } = setup({ offline, apiImpl: albumApi });
  await window.renderAlbum(view, { id: 7 });
  assert.equal(view.querySelectorAll('.album-actions > .btn').length, 3, 'No new button beside Play All');
  const item = albumItem(view);
  assert.equal(item.parentElement.id, 'album-more-menu');
  assert.equal(item.className, 'album-more-item');
  assert.equal(item.textContent.trim(), 'Download offline');
  item.click();
  await settle();
  assert.equal(offline.downloads.length, 1);
  const saved = offline.downloads[0];
  assert.equal(saved.id, 'album:7');
  assert.equal(saved.name, 'LP');
  assert.equal(saved.info.artist_name, 'Band');
  assert.equal(saved.tracks.length, 2);
  assert.ok(seen.toasts.includes('Downloading for offline…'));

  offline.emit({ playlistId: 'album:7', done: 1, total: 2 });
  assert.equal(item.textContent.trim(), 'Downloading 1/2');
  item.click();
  assert.equal(offline.paused, 1);

  offline.stored.add(11);
  offline.emit(null);
  assert.ok(view.querySelector('#tracks-container .track-item').classList.contains('is-offline'));
  assert.equal(item.textContent.trim(), 'Download 1 more');
  offline.stored.add(22);
  offline.emit(null);
  assert.equal(item.textContent.trim(), 'Remove offline copy');
  item.click();
  assert.equal(seen.confirmation.title, 'Remove offline copy?');
  await seen.confirmation.onConfirm();
  assert.deepEqual(offline.removed, ['album:7']);
}

// Album page with the server unreachable: opens from the saved copy.
{
  const offline = fakeOffline({ stored: [11] });
  offline.manifests.set('album:7', { id: 'album:7', name: 'LP', info: { id: 7, title: 'LP', artist_name: 'Band' },
    tracks: [{ id: 11, title: 'Saved A', duration: 60 }], trackIds: [11] });
  const { window, view } = setup({ offline, apiImpl: async () => null });
  await window.renderAlbum(view, { id: '7' });
  assert.ok(view.querySelector('.album-hero-title').textContent.includes('LP'));
  assert.equal(view.querySelectorAll('#tracks-container .track-item').length, 1);
  assert.ok(view.querySelector('#tracks-container .track-item').classList.contains('is-offline'));
}
{
  const { window, view } = setup({ offline: fakeOffline(), apiImpl: async () => null });
  await window.renderAlbum(view, { id: '7' });
  assert.ok(view.textContent.includes('Album not found'));
}

// Albums page with the server unreachable: lists the albums saved here.
{
  const offline = fakeOffline();
  offline.manifests.set('album:7', { id: 'album:7', name: 'LP', info: { id: 7, title: 'LP', artist_name: 'Band' },
    tracks: [{ id: 11 }], trackIds: [11] });
  offline.manifests.set(1, { id: 1, name: 'Mix', tracks: [], trackIds: [] });
  const { window, view } = setup({ offline, apiImpl: async () => null });
  await window.renderAlbums(view);
  const cards = view.querySelectorAll('#albums-grid .card');
  assert.equal(cards.length, 1);
  assert.ok(cards[0].textContent.includes('LP'));
  assert.match(view.querySelector('.section-sub').textContent, /1 album saved on this device/);
}

// Deleting an album from the library also frees its offline copy.
{
  const offline = fakeOffline({ stored: [11, 22] });
  const { window, view, seen } = setup({ offline, apiImpl: async (path, options = {}) => (options.method === 'DELETE'
    ? { success: true } : albumApi(path)) });
  await window.renderAlbum(view, { id: 7 });
  view.querySelector('#del-album-btn').click();
  await seen.confirmation.onConfirm();
  assert.deepEqual(offline.removed, ['album:7']);
}

windows.forEach(window => window.close());
console.log('frontend_offline_playlist_ui=OK');
