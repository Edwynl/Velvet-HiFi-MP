/* VELVET offline-store: playlists downloaded to this device for offline playback.
 * Classic script (globals) like the other extracted modules.
 *
 * Tracks are stored once per track id; playlists are manifests that reference
 * them, so removing one playlist never deletes a track another still uses.
 * Audio is handed to <audio> as a blob: URL rather than served back through
 * the service worker, so WebKit itself handles seeking and lock-screen reads.
 * Object URLs are created ahead of time (warm) because iOS refuses play() when
 * an IndexedDB read sits between the user's tap and the play() call.
 */
const OFFLINE_WARM_LIMIT = 4;
const OFFLINE_FETCH_ATTEMPTS = 3;

function createOfflineManager({ store, fetchTrack, createObjectURL, revokeObjectURL, persist = null }) {
  const stored = new Set();
  const warmUrls = new Map(); // track id -> object URL; insertion order is least recent first
  const listeners = new Set();
  let current = null;

  const snapshot = () => current && { playlistId: current.playlistId, done: current.done, total: current.total };
  const emit = () => {
    const job = snapshot();
    listeners.forEach(listener => { try { listener(job); } catch (error) { console.warn('[VELVET] offline listener', error); } });
  };
  const isQuotaError = error => error?.name === 'QuotaExceededError';

  async function fetchComplete(id) {
    let lastError;
    for (let attempt = 0; attempt < OFFLINE_FETCH_ATTEMPTS; attempt++) {
      try {
        const { blob, contentLength } = await fetchTrack(id);
        if (contentLength != null && blob.size !== contentLength) {
          throw new Error(`Incomplete download: ${blob.size}/${contentLength} bytes`);
        }
        return blob;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError;
  }

  function forget(id) {
    stored.delete(id);
    const url = warmUrls.get(id);
    if (url) { warmUrls.delete(id); revokeObjectURL(url); }
  }

  const manager = {
    async init() {
      (await store.listTrackIds()).forEach(id => stored.add(Number(id)));
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
      if (!record?.blob) return '';
      if (warmUrls.has(id)) return manager.load(id); // A concurrent load won the race.
      const url = createObjectURL(record.blob);
      warmUrls.set(id, url);
      while (warmUrls.size > OFFLINE_WARM_LIMIT) {
        const [oldestId, oldestUrl] = warmUrls.entries().next().value;
        warmUrls.delete(oldestId);
        revokeObjectURL(oldestUrl);
      }
      return url;
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

    pause() {
      if (current) current.paused = true;
    },

    onChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    async download(playlist) {
      if (current) return { downloaded: 0, failed: 0, stopped: 'busy' };
      const playlistId = Number(playlist.id);
      const trackIds = playlist.tracks.map(track => Number(track.id));
      current = { playlistId, done: 0, total: trackIds.length, paused: false };
      const result = { downloaded: 0, failed: 0, stopped: null };
      try {
        await store.putPlaylist({
          id: playlistId,
          name: playlist.name || '',
          description: playlist.description || '',
          tracks: playlist.tracks,
          trackIds,
          savedAt: Date.now(),
        });
        current.done = trackIds.filter(id => stored.has(id)).length;
        emit();
        for (const id of trackIds) {
          if (stored.has(id)) continue;
          if (current.paused) { result.stopped = 'paused'; break; }
          let blob;
          try {
            blob = await fetchComplete(id);
          } catch (error) {
            console.warn('[VELVET] offline download skipped track', id, error?.message || error);
            result.failed++;
            continue;
          }
          try {
            await store.putTrack({ id, blob, size: blob.size, type: blob.type || '', savedAt: Date.now() });
          } catch (error) {
            if (isQuotaError(error)) { result.stopped = 'quota'; break; }
            console.warn('[VELVET] offline save failed', id, error);
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
      const stillUsed = new Set();
      (await store.listPlaylists()).forEach(other => other.trackIds.forEach(trackId => stillUsed.add(trackId)));
      const orphans = manifest.trackIds.filter(trackId => !stillUsed.has(trackId));
      await store.deleteTracks(orphans);
      orphans.forEach(forget);
      emit();
    },

    getPlaylist: playlistId => store.getPlaylist(Number(playlistId)),

    listPlaylists: () => store.listPlaylists(),

    async usage() {
      let bytes = 0;
      for (const id of await store.listTrackIds()) {
        bytes += (await store.getTrack(id))?.size || 0;
      }
      return bytes;
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
      db.createObjectStore('tracks', { keyPath: 'id' });
      db.createObjectStore('playlists', { keyPath: 'id' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { opening = null; reject(request.error); };
  }));
  // Resolve when the transaction commits, so a QuotaExceededError on commit
  // rejects instead of passing as a successful write.
  const run = async (storeName, mode, operation) => {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, mode);
      const request = operation(tx.objectStore(storeName));
      let result;
      if (request) request.onsuccess = () => { result = request.result; };
      tx.oncomplete = () => resolve(result);
      tx.onerror = tx.onabort = () => reject(tx.error || request?.error);
    });
  };
  return {
    listTrackIds: () => run('tracks', 'readonly', os => os.getAllKeys()).then(keys => keys || []),
    getTrack: id => run('tracks', 'readonly', os => os.get(id)).then(value => value || null),
    putTrack: record => run('tracks', 'readwrite', os => os.put(record)),
    deleteTracks: ids => run('tracks', 'readwrite', os => { ids.forEach(id => os.delete(id)); }),
    listPlaylists: () => run('playlists', 'readonly', os => os.getAll()).then(values => values || []),
    getPlaylist: id => run('playlists', 'readonly', os => os.get(id)).then(value => value || null),
    putPlaylist: record => run('playlists', 'readwrite', os => os.put(record)),
    deletePlaylist: id => run('playlists', 'readwrite', os => os.delete(id)),
  };
}

if (typeof window !== 'undefined' && window.indexedDB) {
  let getTicket = async () => '';
  const manager = createOfflineManager({
    store: createIdbOfflineStore(window.indexedDB),
    // The plain stream URL: original file for browser-playable codecs, the
    // server's stereo compatibility stream otherwise. Never DSP or upsampled.
    async fetchTrack(id) {
      const ticket = await getTicket();
      const url = `/api/stream/${encodeURIComponent(id)}?upsample=none` +
        (ticket ? `&ticket=${encodeURIComponent(ticket)}` : '');
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const length = Number(response.headers.get('Content-Length'));
      const blob = await response.blob();
      return { blob, contentLength: Number.isFinite(length) && length > 0 ? length : null };
    },
    createObjectURL: blob => URL.createObjectURL(blob),
    revokeObjectURL: url => URL.revokeObjectURL(url),
    persist: () => window.navigator.storage?.persist?.(),
  });
  manager.configure = options => { if (options?.getTicket) getTicket = options.getTicket; };
  window.VelvetOffline = manager;
}
