/* VELVET offline-store: playlists downloaded to this device for offline playback.
 * Classic script (globals) like the other extracted modules.
 *
 * Tracks are stored once per track id; playlists are manifests that reference
 * them, so removing one playlist never deletes a track another still uses.
 * Audio is streamed into IndexedDB in bounded chunks (a hi-res FLAC or a
 * transcoded WAV can be hundreds of MB; holding it whole in memory gets the
 * PWA killed on iOS) and reassembled as one Blob of disk-backed parts.
 * It is handed to <audio> as a blob: URL rather than served back through the
 * service worker, so WebKit itself handles seeking and lock-screen reads.
 * Object URLs are created ahead of time (warm) because iOS refuses play() when
 * an IndexedDB read sits between the user's tap and the play() call.
 */
const OFFLINE_WARM_LIMIT = 4;
const OFFLINE_FETCH_ATTEMPTS = 3;
const OFFLINE_CHUNK_BYTES = 4 * 1024 * 1024;
const OFFLINE_RETRY_DELAYS_MS = [1000, 3000];

function createOfflineManager({
  store, fetchTrack, createObjectURL, revokeObjectURL, persist = null,
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
}) {
  const stored = new Set();
  const warmUrls = new Map(); // track id -> object URL; insertion order is least recent first
  const listeners = new Set();
  let current = null;
  // The track <audio> is playing from its blob: URL. That URL is never revoked
  // while pinned; a removal parks it here until another track takes over.
  let pinnedId = null;
  let releaseOnUnpin = null;

  const snapshot = () => current && { playlistId: current.playlistId, done: current.done, total: current.total };
  const emit = () => {
    const job = snapshot();
    listeners.forEach(listener => { try { listener(job); } catch (error) { console.warn('[VELVET] offline listener', error); } });
  };
  const isQuotaError = error => error?.name === 'QuotaExceededError';

  // Streams one track into chunks; resolves its metadata record once the
  // whole body arrived and passed the size checks. Partial chunks are removed.
  async function saveTrack(track, signal) {
    const id = Number(track.id);
    await store.deleteChunks(id); // Leftovers of an interrupted earlier attempt.
    try {
      const { chunks, contentLength = null, minBytes = null, type = '' } = await fetchTrack(track, signal);
      let parts = [];
      let partBytes = 0;
      let index = 0;
      let size = 0;
      const flush = async () => {
        if (!parts.length) return;
        await store.putChunk({ id, index: index++, blob: new Blob(parts, { type }) });
        parts = [];
        partBytes = 0;
      };
      for await (const piece of chunks) {
        parts.push(piece);
        partBytes += piece.byteLength;
        size += piece.byteLength;
        if (partBytes >= OFFLINE_CHUNK_BYTES) await flush();
      }
      await flush();
      if (contentLength != null && size !== contentLength) {
        throw new Error(`Incomplete download: ${size}/${contentLength} bytes`);
      }
      if (minBytes != null && size < minBytes) {
        throw new Error(`Incomplete transcode: ${size} bytes, expected at least ${minBytes}`);
      }
      return { id, size, type, chunks: index, savedAt: Date.now() };
    } catch (error) {
      await store.deleteChunks(id).catch(() => {});
      throw error;
    }
  }

  // Throws the last error after OFFLINE_FETCH_ATTEMPTS, backing off between
  // attempts. Quota errors and a pause end it at once.
  async function saveWithRetries(track, job) {
    let lastError;
    for (let attempt = 0; attempt < OFFLINE_FETCH_ATTEMPTS; attempt++) {
      if (attempt) await sleep(OFFLINE_RETRY_DELAYS_MS[attempt - 1]);
      if (job.paused) break;
      try {
        return await saveTrack(track, job.controller.signal);
      } catch (error) {
        if (isQuotaError(error) || job.paused) throw error;
        lastError = error;
      }
    }
    throw lastError;
  }

  function release(url) {
    if (url) revokeObjectURL(url);
  }

  function forget(id) {
    stored.delete(id);
    const url = warmUrls.get(id);
    if (!url) return;
    warmUrls.delete(id);
    if (id === pinnedId) releaseOnUnpin = url;
    else release(url);
  }

  // Deletes the given tracks unless an offline playlist still references them.
  async function deleteUnreferenced(trackIds) {
    const stillUsed = new Set();
    (await store.listPlaylists()).forEach(other => other.trackIds.forEach(trackId => stillUsed.add(trackId)));
    const orphans = trackIds.filter(trackId => !stillUsed.has(trackId));
    if (!orphans.length) return;
    await store.deleteTracks(orphans);
    orphans.forEach(forget);
  }

  const manager = {
    async init() {
      (await store.listTrackIds()).forEach(id => stored.add(Number(id)));
      emit(); // Pages rendered before the index loaded refresh their buttons and markers.
    },

    has: id => stored.has(Number(id)),

    urlFor: id => warmUrls.get(Number(id)) || '',

    async load(trackId) {
      const id = Number(trackId);
      const existing = warmUrls.get(id);
      if (existing) {
        warmUrls.delete(id);
        warmUrls.set(id, existing);
        return existing;
      }
      if (!stored.has(id)) return '';
      const record = await store.getTrack(id);
      const parts = record ? await store.getChunks(id) : [];
      if (!parts.length) return '';
      if (warmUrls.has(id)) return manager.load(id); // A concurrent load won the race.
      const url = createObjectURL(new Blob(parts, { type: record.type || '' }));
      warmUrls.set(id, url);
      while (warmUrls.size > OFFLINE_WARM_LIMIT) {
        const oldestId = [...warmUrls.keys()].find(key => key !== pinnedId);
        release(warmUrls.get(oldestId));
        warmUrls.delete(oldestId);
      }
      return url;
    },

    // Marks the track whose blob: URL <audio> now uses (null: none).
    pin(trackId) {
      pinnedId = trackId == null ? null : Number(trackId);
      if (releaseOnUnpin && !(pinnedId != null && warmUrls.get(pinnedId) === releaseOnUnpin)) {
        release(releaseOnUnpin);
        releaseOnUnpin = null;
      }
    },

    async warm(trackIds) {
      for (const id of trackIds) {
        if (manager.has(id)) await manager.load(id);
      }
    },

    status(playlist) {
      const total = playlist.tracks.length;
      const downloaded = playlist.tracks.filter(track => manager.has(track.id)).length;
      return { total, downloaded, missing: total - downloaded };
    },

    job: snapshot,

    // Stops now: the track in flight is abandoned and fetched again on resume.
    pause() {
      if (!current) return;
      current.paused = true;
      current.controller.abort();
    },

    onChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    async download(playlist) {
      if (current) return { downloaded: 0, failed: 0, stopped: 'busy' };
      const playlistId = Number(playlist.id);
      const trackIds = playlist.tracks.map(track => Number(track.id));
      current = { playlistId, done: 0, total: trackIds.length, paused: false, controller: new AbortController() };
      const result = { downloaded: 0, failed: 0, stopped: null };
      try {
        const previous = await store.getPlaylist(playlistId);
        try {
          await store.putPlaylist({
            id: playlistId,
            name: playlist.name || '',
            description: playlist.description || '',
            tracks: playlist.tracks,
            trackIds,
            savedAt: Date.now(),
          });
        } catch (error) {
          if (!isQuotaError(error)) throw error;
          result.stopped = 'quota';
          return result;
        }
        // Tracks removed from the playlist on the server since the last download.
        if (previous) await deleteUnreferenced(previous.trackIds.filter(id => !trackIds.includes(id)));
        current.done = trackIds.filter(id => stored.has(id)).length;
        emit();
        for (const track of playlist.tracks) {
          const id = Number(track.id);
          if (stored.has(id)) continue;
          if (current.paused) { result.stopped = 'paused'; break; }
          try {
            const record = await saveWithRetries(track, current);
            await store.putTrack(record).catch(async error => {
              await store.deleteChunks(id).catch(() => {}); // No record means no track: free its audio.
              throw error;
            });
          } catch (error) {
            if (current.paused) { result.stopped = 'paused'; break; }
            if (isQuotaError(error)) { result.stopped = 'quota'; break; }
            console.warn('[VELVET] offline download skipped track', id, error?.message || error);
            result.failed++;
            continue;
          }
          stored.add(id);
          result.downloaded++;
          current.done++;
          emit();
        }
      } finally {
        current = null;
        emit();
      }
      if (result.downloaded && persist) {
        try { await persist(); } catch { /* Best effort: storage may still be evicted. */ }
      }
      return result;
    },

    async remove(playlistId) {
      const id = Number(playlistId);
      const manifest = await store.getPlaylist(id);
      await store.deletePlaylist(id);
      if (!manifest) return;
      await deleteUnreferenced(manifest.trackIds);
      emit();
    },

    getPlaylist: playlistId => store.getPlaylist(Number(playlistId)),

    listPlaylists: () => store.listPlaylists(),

    async usage() {
      return (await store.listTracks()).reduce((total, record) => total + (record.size || 0), 0);
    },
  };
  return manager;
}

function createIdbOfflineStore(idb, name = 'velvet-offline') {
  let opening = null;
  const open = () => opening || (opening = new Promise((resolve, reject) => {
    const request = idb.open(name, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      db.createObjectStore('tracks', { keyPath: 'id' });           // {id, size, type, chunks, savedAt}
      db.createObjectStore('chunks', { keyPath: ['id', 'index'] }); // {id, index, blob}
      db.createObjectStore('playlists', { keyPath: 'id' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { opening = null; reject(request.error); };
  }));
  // Resolve when the transaction commits, so a QuotaExceededError on commit
  // rejects instead of passing as a successful write.
  const run = async (storeNames, mode, operation) => {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeNames, mode);
      const request = operation(tx);
      let result;
      if (request) request.onsuccess = () => { result = request.result; };
      tx.oncomplete = () => resolve(result);
      tx.onerror = tx.onabort = () => reject(tx.error || request?.error);
    });
  };
  const chunkRange = id => IDBKeyRange.bound([id, 0], [id, Infinity]);
  return {
    listTrackIds: () => run('tracks', 'readonly', tx => tx.objectStore('tracks').getAllKeys()).then(keys => keys || []),
    listTracks: () => run('tracks', 'readonly', tx => tx.objectStore('tracks').getAll()).then(values => values || []),
    getTrack: id => run('tracks', 'readonly', tx => tx.objectStore('tracks').get(id)).then(value => value || null),
    putTrack: record => run('tracks', 'readwrite', tx => tx.objectStore('tracks').put(record)),
    putChunk: record => run('chunks', 'readwrite', tx => tx.objectStore('chunks').put(record)),
    getChunks: id => run('chunks', 'readonly', tx => tx.objectStore('chunks').getAll(chunkRange(id)))
      .then(values => (values || []).map(value => value.blob)),
    deleteChunks: id => run('chunks', 'readwrite', tx => tx.objectStore('chunks').delete(chunkRange(id))),
    deleteTracks: ids => run(['tracks', 'chunks'], 'readwrite', tx => {
      ids.forEach(id => {
        tx.objectStore('tracks').delete(id);
        tx.objectStore('chunks').delete(chunkRange(id));
      });
    }),
    listPlaylists: () => run('playlists', 'readonly', tx => tx.objectStore('playlists').getAll()).then(values => values || []),
    getPlaylist: id => run('playlists', 'readonly', tx => tx.objectStore('playlists').get(id)).then(value => value || null),
    putPlaylist: record => run('playlists', 'readwrite', tx => tx.objectStore('playlists').put(record)),
    deletePlaylist: id => run('playlists', 'readwrite', tx => tx.objectStore('playlists').delete(id)),
  };
}

async function* readResponseChunks(body) {
  const reader = body.getReader();
  let finished = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) { finished = true; return; }
      yield value;
    }
  } finally {
    if (!finished) reader.cancel().catch(() => {});
  }
}

if (typeof window !== 'undefined' && window.indexedDB) {
  let getTicket = async () => '';
  const manager = createOfflineManager({
    store: createIdbOfflineStore(window.indexedDB),
    // The plain stream URL: original file for browser-playable codecs, the
    // server's stereo compatibility stream otherwise. Never DSP or upsampled.
    async fetchTrack(track, signal) {
      const ticket = await getTicket();
      const url = `/api/stream/${encodeURIComponent(track.id)}?upsample=none` +
        (ticket ? `&ticket=${encodeURIComponent(ticket)}` : '');
      const response = await fetch(url, { cache: 'no-store', signal });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const length = Number(response.headers.get('Content-Length'));
      const contentLength = Number.isFinite(length) && length > 0 ? length : null;
      let minBytes = null;
      if (contentLength == null) {
        // The compatibility transcode streams without a length: stereo s32 PCM
        // at the source rate, 8 bytes per frame. Without duration and rate a
        // cut-off transcode cannot be told from a complete one.
        const duration = Number(track.duration);
        const rate = Number(track.sample_rate);
        if (!(duration > 0 && rate > 0)) {
          response.body.cancel().catch(() => {});
          throw new Error('Cannot verify a download without a length');
        }
        minBytes = Math.floor(Math.max(0, duration * 0.98 - 0.5) * rate * 8);
      }
      const type = (response.headers.get('Content-Type') || '').split(';')[0].trim();
      return { chunks: readResponseChunks(response.body), contentLength, minBytes, type };
    },
    createObjectURL: blob => URL.createObjectURL(blob),
    revokeObjectURL: url => URL.revokeObjectURL(url),
    persist: () => window.navigator.storage?.persist?.(),
  });
  manager.configure = options => { if (options?.getTicket) getTicket = options.getTicket; };
  window.VelvetOffline = manager;
}
