/* Velvet Remote Bridge: share this page's play queue with the bridge.
 *
 * Added by the bridge to the parent Velvet page it relays. Velvet's own
 * auto-next runs in this page, and iOS suspends the page when the phone
 * locks, so the bridge plays the queue on by itself. This script:
 *   - adds the queue to every cast request (bridge_queue: ids, index, repeat);
 *   - posts queue changes (added, removed, reordered, repeat) while casting;
 *   - when the bridge advanced on its own (phone locked), moves the page to
 *     that track through Velvet's own playTrack(); the bridge recognises the
 *     resulting cast as the track already playing and does not restart it.
 * It changes nothing when the page is not casting through the bridge.
 */
(() => {
  'use strict';
  // Velvet's page globals (classic-script bindings, not window properties).
  if (typeof state !== 'object' || typeof playTrack !== 'function' || typeof isCastZone !== 'function') return;

  const MAX_QUEUE = 5000;
  const SYNC_MS = 2000;
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

  let lastSent = '';
  let lastFollowed = 0;
  let busy = false;

  async function sync() {
    if (busy || !isCastZone() || !state.castDevice) return;
    busy = true;
    try {
      const udn = state.castDevice.udn;
      const response = await realFetch(`/bridge/session/${udn}`, { headers: headers(), cache: 'no-store' });
      if (!response.ok) return;
      const session = await response.json();
      if (!session.active) return;
      const current = state.currentTrack ? state.currentTrack.id : null;
      if (session.advance_seq > lastFollowed && session.track_id !== current
          && !state.castPending && !state.castControlPending
          && Number(state.queue[session.queue_index] && state.queue[session.queue_index].id) === session.track_id) {
        // The bridge played on while this page slept: show that track.
        lastFollowed = session.advance_seq;
        state.queueIdx = session.queue_index;
        playTrack(state.queue[session.queue_index]);
        return;
      }
      if (session.track_id !== current) return;
      lastFollowed = Math.max(lastFollowed, session.advance_seq);
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
  window.__velvetBridgeQueueSync = { sync, queueFor };
})();
