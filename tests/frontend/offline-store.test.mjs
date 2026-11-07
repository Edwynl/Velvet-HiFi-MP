import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// The offline manager is pure logic over an injected store, so it runs in a
// bare VM with an in-memory store. The IndexedDB adapter is exercised in the
// browser; nothing here may depend on window/indexedDB existing.
const source = readFileSync('static/js/offline-store.js', 'utf8');
const sandbox = vm.createContext({ Promise, Map, Set, Date, Error, Blob, Uint8Array, console });
vm.runInContext(source, sandbox);
assert.equal(sandbox.VelvetOffline, undefined, 'No IndexedDB means no global instance');

function memoryStore() {
  const tracks = new Map();
  const chunks = new Map(); // `${id}:${index}` -> blob
  const playlists = new Map();
  const quotaError = () => { const e = new Error('full'); e.name = 'QuotaExceededError'; return e; };
  const store = {
    tracks, chunks, playlists, failChunk: null, maxChunkBytes: 0,
    async listTrackIds() { return [...tracks.keys()]; },
    async listTracks() { return [...tracks.values()]; },
    async getTrack(id) { return tracks.get(id) || null; },
    async putTrack(rec) { tracks.set(rec.id, rec); },
    async putChunk(rec) {
      if (store.failChunk?.(rec)) throw quotaError();
      store.maxChunkBytes = Math.max(store.maxChunkBytes, rec.blob.size);
      chunks.set(`${rec.id}:${rec.index}`, rec.blob);
    },
    async getChunks(id) {
      return [...chunks.entries()].filter(([key]) => key.startsWith(`${id}:`))
        .sort(([a], [b]) => Number(a.split(':')[1]) - Number(b.split(':')[1])).map(([, blob]) => blob);
    },
    async deleteChunks(id) { [...chunks.keys()].filter(key => key.startsWith(`${id}:`)).forEach(key => chunks.delete(key)); },
    async deleteTracks(ids) { for (const id of ids) { tracks.delete(id); await store.deleteChunks(id); } },
    async listPlaylists() { return [...playlists.values()]; },
    async getPlaylist(id) { return playlists.get(id) || null; },
    async putPlaylist(rec) { playlists.set(rec.id, rec); },
    async deletePlaylist(id) { playlists.delete(id); },
  };
  return store;
}

const bytes = n => new Uint8Array(n);
// A body delivered in pieces, the way a ReadableStream reader yields it.
const body = (...sizes) => sizes.map(bytes);

function setup({ fetchTrack } = {}) {
  const store = memoryStore();
  const calls = [];
  const revoked = [];
  const urlBlobs = new Map();
  let urls = 0;
  const manager = sandbox.createOfflineManager({
    store,
    fetchTrack: fetchTrack || (async track => {
      calls.push(track.id);
      return { chunks: body(4, 6), contentLength: 10, minBytes: null, type: 'audio/flac' };
    }),
    createObjectURL: blob => { const url = `blob:velvet/${++urls}`; urlBlobs.set(url, blob); return url; },
    revokeObjectURL: url => revoked.push(url),
  });
  return { store, manager, calls, revoked, urlBlobs };
}

const playlist = (id, ids) => ({ id, name: `P${id}`, tracks: ids.map(t => ({ id: t, title: `T${t}` })) });

// Downloads every track and reports status.
{
  const { manager, calls } = setup();
  await manager.init();
  const result = await manager.download(playlist(1, [11, 22]));
  assert.equal(result.downloaded, 2);
  assert.equal(result.failed, 0);
  assert.ok(manager.has(11) && manager.has(22));
  assert.deepEqual({ ...manager.status(playlist(1, [11, 22])) }, { total: 2, downloaded: 2, missing: 0 });
  // Resume: a second run fetches nothing already stored.
  await manager.download(playlist(1, [11, 22, 33]));
  assert.deepEqual([...calls], [11, 22, 33]);
  assert.equal((await manager.getPlaylist(1)).tracks.length, 3, 'Manifest keeps the latest track list');
}

// The body is written in bounded chunks and reassembled for playback.
{
  const { store, manager, urlBlobs } = setup({ fetchTrack: async () => ({
    chunks: body(3 * 1024 * 1024, 3 * 1024 * 1024, 3 * 1024 * 1024), contentLength: 9 * 1024 * 1024, type: 'audio/flac',
  }) });
  await manager.download(playlist(1, [11]));
  assert.ok(store.chunks.size >= 2, 'A large track is split into several chunks');
  assert.ok(store.maxChunkBytes <= 6 * 1024 * 1024, 'No chunk holds the whole file');
  const url = await manager.load(11);
  assert.equal(urlBlobs.get(url).size, 9 * 1024 * 1024);
  assert.equal(urlBlobs.get(url).type, 'audio/flac');
  assert.equal(await manager.usage(), 9 * 1024 * 1024);
}

// init() reloads what a previous session stored.
{
  const { store, manager } = setup();
  await manager.download(playlist(1, [11]));
  const again = sandbox.createOfflineManager({ store, fetchTrack: async () => { throw new Error('no'); },
    createObjectURL: () => 'blob:x', revokeObjectURL() {} });
  assert.equal(again.has(11), false);
  await again.init();
  assert.equal(again.has(11), true);
}

// Removing a playlist keeps tracks another offline playlist still uses.
{
  const { store, manager } = setup();
  await manager.download(playlist(1, [11, 22]));
  await manager.download(playlist(2, [22, 33]));
  await manager.remove(1);
  assert.equal(manager.has(11), false);
  assert.equal(manager.has(22), true);
  assert.equal(manager.has(33), true);
  assert.equal([...store.chunks.keys()].some(key => key.startsWith('11:')), false, 'Audio of removed tracks is deleted');
  assert.equal(await manager.getPlaylist(1), null);
  assert.equal((await manager.listPlaylists()).length, 1);
}

// Re-downloading a playlist that lost tracks on the server frees them, unless
// another offline playlist still uses them.
{
  const { manager } = setup();
  await manager.download(playlist(1, [11, 22, 33]));
  await manager.download(playlist(2, [33]));
  await manager.download(playlist(1, [11]));
  assert.equal(manager.has(22), false);
  assert.equal(manager.has(33), true);
}

// A truncated response is never stored as complete, and leaves no chunks.
{
  const { store, manager } = setup({ fetchTrack: async () => ({ chunks: body(5), contentLength: 10, type: 'audio/flac' }) });
  const result = await manager.download(playlist(1, [11]));
  assert.equal(manager.has(11), false);
  assert.equal(result.failed, 1);
  assert.equal(store.chunks.size, 0);
}

// A transcoded stream has no Content-Length; minBytes guards it instead.
{
  const { manager } = setup({ fetchTrack: async track => ({
    chunks: body(track.id === 11 ? 50 : 100), contentLength: null, minBytes: 90, type: 'audio/wav',
  }) });
  const result = await manager.download(playlist(1, [11, 22]));
  assert.equal(manager.has(11), false, 'Short transcode is rejected');
  assert.equal(manager.has(22), true);
  assert.equal(result.failed, 1);
}

// A track failing three times is skipped; the rest still download.
{
  const attempts = new Map();
  const { manager } = setup({ fetchTrack: async track => {
    attempts.set(track.id, (attempts.get(track.id) || 0) + 1);
    if (track.id === 11) throw new Error('boom');
    return { chunks: body(1), contentLength: null, type: 'audio/flac' };
  } });
  const result = await manager.download(playlist(1, [11, 22]));
  assert.equal(attempts.get(11), 3);
  assert.equal(result.failed, 1);
  assert.equal(result.downloaded, 1);
  assert.equal(manager.has(22), true);
}

// Storage full stops the whole job and keeps what was saved.
{
  const { store, manager } = setup();
  store.failChunk = rec => rec.id === 22;
  const result = await manager.download(playlist(1, [11, 22, 33]));
  assert.equal(result.stopped, 'quota');
  assert.equal(manager.has(11), true);
  assert.equal(manager.has(22), false);
  assert.equal(manager.has(33), false);
}

// pause() stops after the current track; job() reports progress meanwhile.
{
  let manager;
  const seen = [];
  ({ manager } = setup({ fetchTrack: async track => {
    seen.push({ ...manager.job() });
    if (track.id === 11) manager.pause();
    return { chunks: body(1), contentLength: 1, type: 'audio/flac' };
  } }));
  const events = [];
  const off = manager.onChange(job => events.push(job && { ...job }));
  const result = await manager.download(playlist(1, [11, 22]));
  assert.equal(result.stopped, 'paused');
  assert.equal(manager.has(11), true);
  assert.equal(manager.has(22), false);
  assert.deepEqual(seen[0], { playlistId: 1, done: 0, total: 2 });
  assert.equal(manager.job(), null);
  assert.ok(events.some(e => e && e.done === 1), 'Progress is published');
  assert.equal(events.at(-1), null, 'The end of a job is published');
  off();
  await manager.download(playlist(1, [11, 22]));
  assert.equal(events.at(-1), null);
  assert.ok(!events.some(e => e && e.total === 2 && e.done === 2), 'Unsubscribed listeners hear nothing');
}

// warm() keeps at most four object URLs alive.
{
  const { manager, revoked } = setup();
  await manager.download(playlist(1, [1, 2, 3, 4, 5, 6]));
  assert.equal(manager.urlFor(1), '', 'Nothing is warm before warm()');
  await manager.warm([1, 2, 3, 4, 5, 6]);
  assert.equal(revoked.length, 2);
  assert.equal(manager.urlFor(1), '');
  assert.ok(manager.urlFor(6).startsWith('blob:'));
  assert.ok(manager.urlFor(3).startsWith('blob:'));
  assert.ok((await manager.load(1)).startsWith('blob:'));
  assert.equal(await manager.load(999), '', 'Unknown tracks have no URL');
  assert.equal(await manager.usage(), 60);
}

console.log('frontend_offline_store=OK');
