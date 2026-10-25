/* Velvet Remote Bridge: share this page's play queue with the bridge.
 *
 * Added by the bridge to the parent Velvet page it relays. Velvet's own
 * auto-next runs in this page, and iOS suspends the page when the phone
 * locks (or discards it), so the bridge plays the queue on by itself. This
 * script:
 *   - adds the queue to every cast request (bridge_queue: ids, index, repeat);
 *   - posts queue changes (added, removed, reordered, repeat) while casting;
 *   - shows what the renderer plays when the bridge moved on without this
 *     page (its own auto-next, or another phone), without casting anything;
 *   - on a freshly loaded page (Safari or the PWA reloaded it), reattaches to
 *     the session still playing, again without casting anything.
 * It changes nothing when the page is not casting through the bridge and no
 * bridge session is playing.
 */
(() => {
  'use strict';
  // Velvet's page globals (classic-script bindings, not window properties).
  if (typeof state !== 'object' || typeof playTrack !== 'function' || typeof isCastZone !== 'function') return;

  const MAX_QUEUE = 5000;
  const SYNC_MS = 2000;
  const METADATA_FETCHES = 4; // in parallel, for a reattached queue
  const CAST_PATH = /^\/api\/cast\/[^/]+$/;
  const REPEAT_MODES = ['none', 'all', 'one'];
  const realFetch = window.fetch.bind(window);

  // The queue with `trackId` as its current entry, or null if unusable.
  function queueFor(trackId) {
    let ids = state.queue.map(track => Number(track && track.id));
    if (!ids.every(id => Number.isInteger(id) && id > 0)) return null;
    let index = ids[state.queueIdx] === trackId ? state.queueIdx : ids.indexOf(trackId);
    if (index < 0) return null;
    if (ids.length > MAX_QUEUE) {
      ids = ids.slice(index, index + MAX_QUEUE);
      index = 0;
    }
    const repeat = REPEAT_MODES.includes(state.repeatMode) ? state.repeatMode : 'none';
    return { ids, index, repeat };
  }

  function headers(extra = {}) {
    const result = { ...extra };
    if (typeof authState === 'object' && authState && authState.apiKey) result['X-API-Key'] = authState.apiKey;
    return result;
  }

  async function getJson(url) {
    const response = await realFetch(url, { headers: headers(), cache: 'no-store' });
    return response.ok ? response.json() : null;
  }

  // Every cast carries its queue, so the bridge knows what follows it.
  window.fetch = function bridgeFetch(input, init) {
    try {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      if (url.origin === location.origin && CAST_PATH.test(url.pathname) && init
          && String(init.method || 'GET').toUpperCase() === 'POST' && typeof init.body === 'string') {
        const body = JSON.parse(init.body);
        const queue = Number.isInteger(body.track_id) ? queueFor(body.track_id) : null;
        if (queue) init = { ...init, body: JSON.stringify({ ...body, bridge_queue: queue }) };
      }
    } catch {
      // Not a cast we understand: send it unchanged.
    }
    return realFetch(input, init);
  };

  // A fresh page may reattach until the user picks an output or plays
  // something here: from then on the page is theirs, never taken over.
  let userTookOver = false;
  if (typeof activateZone === 'function') {
    const velvetActivateZone = activateZone;
    // eslint-disable-next-line no-global-assign
    activateZone = function bridgeActivateZone(...args) {
      userTookOver = true;
      return velvetActivateZone.apply(this, args);
    };
  }

  // The session's queue as Velvet track objects: this page's own objects
  // where it has them, else placeholders filled from the parent afterwards.
  function queueFromSession(ids) {
    const known = new Map(state.queue.filter(Boolean).map(track => [Number(track.id), track]));
    return ids.map(id => known.get(id) || { id, title: '…', artist_name: '', placeholder: true });
  }

  async function fillMetadata(queue, first) {
    const track = queue[first];
    if (track.placeholder) {
      const full = await getJson(`/api/tracks/${track.id}`);
      if (full) Object.assign(track, full, { placeholder: false });
    }
    const pending = queue.filter(entry => entry.placeholder);
    const worker = async () => {
      for (let entry = pending.shift(); entry; entry = pending.shift()) {
        const full = await getJson(`/api/tracks/${entry.id}`).catch(() => null);
        if (full) Object.assign(entry, full, { placeholder: false });
      }
    };
    // Later entries load in the background; the queue panel redraws once.
    Promise.all(Array.from({ length: METADATA_FETCHES }, worker)).then(() => {
      if (typeof renderQueue === 'function' && state.queue === queue) renderQueue();
    });
  }

  // Show what the bridge's session plays, exactly as Velvet does after a
  // confirmed cast, but without casting: the renderer already plays it.
  async function adopt(session, { attach = false } = {}) {
    const queue = queueFromSession(session.ids);
    const index = session.queue_index;
    if (!queue[index] || queue[index].id !== session.track_id) return false;
    const before = { current: state.currentTrack, epoch: state.zoneEpoch, castSeq: state.castSeq };
    await fillMetadata(queue, index);
    // The user acted while the track's details loaded: their action stands.
    if (state.currentTrack !== before.current || state.zoneEpoch !== before.epoch
        || state.castSeq !== before.castSeq || state.castPending || (attach && userTookOver)) return false;
    if (attach) {
      state.outputMode = 'cast';
      state.castDevice = { udn: session.udn, name: session.name, type: 'cast', id: session.udn };
      state.nativeDevice = null;
      const castButton = document.getElementById('btn-cast');
      if (castButton) castButton.style.color = 'var(--gold)';
      if (typeof restoreZoneDspSelection === 'function') restoreZoneDspSelection();
    }
    // A new view of the renderer: older answers belong to the previous one.
    state.zoneEpoch = (state.zoneEpoch || 0) + 1;
    state.castControlSeq = (state.castControlSeq || 0) + 1;
    state.castControlPending = null;
    state.castPending = false;
    state.castStartOffset = 0;
    state.castPosition = Number(session.position) || 0;
    state.queue = queue;
    state.queueIdx = index;
    state.currentTrack = queue[index];
    state.castConfirmedTrackId = queue[index].id;
    state.castConfirmedTrack = queue[index];
    state.castConfirmedQueue = { queue, idx: index };
    state.castPlaybackUnverified = false;
    state.castMismatchNotified = false;
    state.castMismatchPolls = 0;
    state.castAutoNextArmed = true;
    state.playing = session.state === 'PLAYING';
    if (typeof updatePlayerUI === 'function') updatePlayerUI(queue[index]);
    if (typeof updatePlayBtnUI === 'function') updatePlayBtnUI();
    if (typeof updateMediaSession === 'function') updateMediaSession(queue[index]);
    if (typeof startCastPolling === 'function') startCastPolling();
    if (typeof renderQueue === 'function' && document.getElementById('queue-panel')?.classList.contains('open')) renderQueue();
    return true;
  }

  let lastSent = '';
  let seenRevision = 0;
  let seenSkips = '';
  let busy = false;

  function reportSkips(session) {
    const signature = JSON.stringify(session.skipped || []);
    if (signature === seenSkips) return;
    seenSkips = signature;
    if ((session.skipped || []).length && typeof showToast === 'function') {
      showToast(`Skipped ${session.skipped.length} unplayable track(s) in the queue`, 6000);
    }
    if (session.error && typeof showToast === 'function') showToast(session.error, 6000);
  }

  async function sync() {
    if (busy) return;
    busy = true;
    try {
      if (!isCastZone() || !state.castDevice) {
        // A fresh page: reattach to a session still playing or paused.
        if (userTookOver || state.currentTrack) return;
        const session = await getJson('/bridge/session');
        if (session && session.active && !session.idle && !userTookOver && !state.currentTrack) {
          if (await adopt(session, { attach: true })) seenRevision = session.revision;
        }
        return;
      }
      const udn = state.castDevice.udn;
      const session = await getJson(`/bridge/session/${udn}`);
      if (!session || !session.active) return;
      reportSkips(session);
      if (state.castPending || state.castControlPending) return; // our own request decides
      const current = state.currentTrack ? state.currentTrack.id : null;
      if (session.track_id !== current) {
        // The bridge moved on without this page: its own auto-next, or another phone.
        if (session.revision > seenRevision && await adopt(session)) seenRevision = session.revision;
        return;
      }
      seenRevision = Math.max(seenRevision, session.revision);
      const queue = queueFor(current);
      if (!queue) return;
      const signature = JSON.stringify([udn, queue]);
      if (signature === lastSent) return;
      const sent = await realFetch(`/bridge/queue/${udn}`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ track_id: current, ...queue }),
      });
      if (sent.ok) lastSent = signature;
    } catch {
      // Offline or the bridge restarted: try again next time.
    } finally {
      busy = false;
    }
  }

  setInterval(sync, SYNC_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sync();
  });
  // A reloaded page reattaches as soon as Velvet has started up.
  window.addEventListener('load', () => setTimeout(sync, 300));
  window.__velvetBridgeQueueSync = { sync, queueFor, adopt };
})();
