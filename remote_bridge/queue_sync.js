/* Velvet Remote Bridge: share this page's play queue with the bridge.
 *
 * Added by the bridge to the parent Velvet page it relays. Velvet's own
 * auto-next runs in this page, and iOS suspends the page when the phone
 * locks (or discards it), so the bridge plays the queue on by itself. This
 * script:
 *   - adds the queue to every cast request (bridge_queue: ids, index, repeat);
 *   - posts queue changes (added, removed, reordered, repeat) while casting,
 *     each against the session revision this page last saw: the bridge
 *     refuses an edit made before a change from elsewhere;
 *   - takes in changes from elsewhere (the bridge's own auto-next, another
 *     phone's cast or queue edit), merged with this page's unsent edits,
 *     without casting anything;
 *   - on a freshly loaded page (Safari or the PWA reloaded it), reattaches to
 *     the session still playing, repeat mode included, again without casting
 *     or changing anything.
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
  // Entries before and after the current one whose details load with it;
  // the others load when the queue panel shows them.
  const METADATA_BEFORE = 5;
  const METADATA_AFTER = 40;
  const CAST_PATH = /^\/api\/cast\/([^/]+)$/;
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

  const sameIds = (a, b) => a.length === b.length && a.every((id, i) => id === b[i]);

  // The bridge's queue as this page last agreed with it: { udn, revision,
  // ids, repeat }. This page's edits are what differs from it. `revision` is
  // null after this page's own cast until the page sees that cast's session.
  let base = null;

  // Three-way merge of this page's queue (`local`) with the bridge's newer
  // session (`server`), both descended from `from`. Each side's change is
  // kept where only it changed; where both changed the same thing, the
  // bridge's version (already in effect) stands and `conflict` says so.
  function reconcile(from, local, server) {
    const merged = { ids: server.ids, index: server.queue_index, repeat: server.repeat, conflict: false };
    if (!from || !local) return merged;
    if (!sameIds(local.ids, from.ids) && !sameIds(local.ids, server.ids)) {
      let index = local.ids.indexOf(server.track_id, local.index);
      if (index < 0) index = local.ids.indexOf(server.track_id);
      if (sameIds(server.ids, from.ids) && index >= 0) Object.assign(merged, { ids: local.ids, index });
      else merged.conflict = true;
    }
    if (local.repeat !== from.repeat && local.repeat !== server.repeat) {
      if (server.repeat === from.repeat) merged.repeat = local.repeat;
      else merged.conflict = true;
    }
    return merged;
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
      const cast = url.origin === location.origin ? CAST_PATH.exec(url.pathname) : null;
      if (cast && init && String(init.method || 'GET').toUpperCase() === 'POST' && typeof init.body === 'string') {
        const body = JSON.parse(init.body);
        const queue = Number.isInteger(body.track_id) ? queueFor(body.track_id) : null;
        if (queue) {
          init = { ...init, body: JSON.stringify({ ...body, bridge_queue: queue }) };
          // The cast sets the bridge's queue: later edits are relative to it.
          base = { udn: decodeURIComponent(cast[1]), revision: null, ids: queue.ids, repeat: queue.repeat };
        }
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

  async function loadEntry(entry) {
    const full = await getJson(`/api/tracks/${entry.id}`);
    if (full) Object.assign(entry, full, { placeholder: false });
  }

  // A filled-in entry in the open queue panel, changed in place: redrawing
  // the panel would scroll it back to the current track.
  function showRow(entry) {
    const list = document.getElementById('queue-list');
    const row = list && list.children[state.queue.indexOf(entry)];
    if (!row) return;
    const set = (selector, text) => {
      const element = row.querySelector(selector);
      if (element) element.textContent = text;
    };
    set('.queue-item-title', entry.title || '');
    set('.queue-item-sub', entry.artist_name || '');
    if (typeof fmt === 'function') set('.queue-item-dur', fmt(entry.duration));
  }

  // Details of the placeholders at `indices` of `queue`, a few requests at a
  // time; the rest are dropped once `queue` is no longer the page's (another
  // session, or the user's own choice).
  function hydrate(queue, indices) {
    const pending = indices.map(i => queue[i]).filter(entry => entry && entry.placeholder && !entry.loading);
    pending.forEach(entry => { entry.loading = true; });
    const worker = async () => {
      for (let entry = pending.shift(); entry; entry = pending.shift()) {
        if (state.queue === queue) await loadEntry(entry).catch(() => {});
        delete entry.loading;
        if (!entry.placeholder) showRow(entry);
      }
    };
    return Promise.all(Array.from({ length: Math.min(METADATA_FETCHES, pending.length) }, worker));
  }

  function hydrateAround(queue, index) {
    const around = [];
    for (let i = Math.max(0, index - METADATA_BEFORE); i <= Math.min(queue.length - 1, index + METADATA_AFTER); i++) {
      around.push(i);
    }
    hydrate(queue, around);
  }

  // The queue panel: placeholders load as their rows come into view.
  let rowObserver = null;
  function observeRows() {
    if (rowObserver) rowObserver.disconnect();
    rowObserver = null;
    const list = document.getElementById('queue-list');
    const queue = state.queue;
    if (!list || !queue.some(entry => entry && entry.placeholder)) return;
    const observer = rowObserver = new IntersectionObserver(seen => {
      const shown = seen.filter(item => item.isIntersecting).map(item => item.target);
      shown.forEach(row => observer.unobserve(row));
      if (shown.length) hydrate(queue, shown.map(row => Array.prototype.indexOf.call(list.children, row)));
    }, { rootMargin: '300px 0px' });
    Array.from(list.children).forEach((row, i) => {
      if (queue[i] && queue[i].placeholder) observer.observe(row);
    });
  }
  if (typeof renderQueue === 'function' && typeof IntersectionObserver === 'function') {
    const velvetRenderQueue = renderQueue;
    // eslint-disable-next-line no-global-assign
    renderQueue = function bridgeRenderQueue(...args) {
      const result = velvetRenderQueue.apply(this, args);
      observeRows();
      return result;
    };
  }

  function redrawOpenQueue() {
    if (typeof renderQueue === 'function' && document.getElementById('queue-panel')?.classList.contains('open')) renderQueue();
  }

  // Velvet's repeat buttons, as its own click handler leaves them.
  function showRepeat(mode) {
    state.repeatMode = mode;
    const color = mode !== 'none' ? 'var(--gold)' : '';
    for (const id of ['btn-repeat', 'mobile-btn-repeat']) {
      const button = document.getElementById(id);
      if (button) button.style.color = color;
    }
  }

  // Show what the bridge's session plays (with `merged`, its queue and
  // repeat as reconciled), exactly as Velvet does after a confirmed cast,
  // but without casting: the renderer already plays it.
  async function adopt(session, merged, { attach = false } = {}) {
    const queue = queueFromSession(merged.ids);
    const index = merged.index;
    if (!queue[index] || queue[index].id !== session.track_id) return false;
    const before = { current: state.currentTrack, epoch: state.zoneEpoch, castSeq: state.castSeq };
    if (queue[index].placeholder) await loadEntry(queue[index]);
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
    showRepeat(merged.repeat);
    if (typeof updatePlayerUI === 'function') updatePlayerUI(queue[index]);
    if (typeof updatePlayBtnUI === 'function') updatePlayBtnUI();
    if (typeof updateMediaSession === 'function') updateMediaSession(queue[index]);
    if (typeof startCastPolling === 'function') startCastPolling();
    hydrateAround(queue, index);
    redrawOpenQueue();
    return true;
  }

  // The same track plays, its queue or repeat changed (`merged`): show that.
  function applyQueue(merged, local) {
    if (!local || !sameIds(local.ids, merged.ids) || local.index !== merged.index) {
      const queue = queueFromSession(merged.ids);
      state.queue = queue;
      state.queueIdx = merged.index;
      if (state.castConfirmedQueue && state.castConfirmedTrackId === queue[merged.index].id) {
        state.castConfirmedQueue = { queue, idx: merged.index };
      }
      hydrateAround(queue, merged.index);
      redrawOpenQueue();
    }
    if (state.repeatMode !== merged.repeat) showRepeat(merged.repeat);
  }

  function agree(udn, session) {
    base = { udn, revision: session.revision, ids: session.ids, repeat: session.repeat };
  }

  let seenSkips = '';
  let refused = ''; // an edit whose administrator prompt the user cancelled
  let resyncing = false; // the last post was refused (409): no second immediate retry
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
        const session = await getJson('/api/bridge/session');
        if (session && session.active && !session.idle && !userTookOver && !state.currentTrack
            && await adopt(session, reconcile(null, null, session), { attach: true })) agree(session.udn, session);
        return;
      }
      const udn = state.castDevice.udn;
      const session = await getJson(`/api/bridge/session/${udn}`);
      if (!session || !session.active) return;
      reportSkips(session);
      if (state.castPending || state.castControlPending) return; // our own request decides
      const ours = base && base.udn === udn ? base : null;
      if (ours && ours.revision !== null && session.revision < ours.revision) return; // an older answer
      if (!ours || session.revision !== ours.revision) {
        // A change this page has not seen: the bridge's own advance, or
        // another phone's cast or queue edit. Keep this page's own edits.
        const current = state.currentTrack ? state.currentTrack.id : null;
        const local = current === null ? null : queueFor(current);
        const merged = reconcile(ours, local, session);
        if (session.track_id !== current) {
          if (!await adopt(session, merged)) return;
        } else {
          applyQueue(merged, local);
        }
        if (merged.conflict && typeof showToast === 'function') {
          showToast('The queue was changed on another device: showing that version', 5000);
        }
        agree(udn, session);
      }
      if (!state.currentTrack || state.currentTrack.id !== session.track_id) return; // ours is on its way
      const queue = queueFor(session.track_id);
      if (!queue || (sameIds(queue.ids, base.ids) && queue.repeat === base.repeat)) return;
      const signature = JSON.stringify([udn, base.revision, queue]);
      if (signature === refused) return;
      // Velvet's managementFetch asks for the administrator password if this
      // browser session has not unlocked it yet, then sends again.
      const send = typeof managementFetch === 'function' ? managementFetch : realFetch;
      const sent = await send(`/api/bridge/queue/${udn}`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ track_id: session.track_id, ...queue, expected_revision: base.revision }),
      });
      const conflicted = sent.status === 409;
      if (sent.ok) {
        const answer = await sent.json();
        if (answer.success) base = { udn, revision: answer.revision, ids: queue.ids, repeat: queue.repeat };
      } else if (conflicted && !resyncing) {
        setTimeout(sync, 0); // a change from elsewhere came first: merge it now (once; then every SYNC_MS)
      } else if (sent.adminCancelled) {
        refused = signature; // ask again only for another edit
      }
      resyncing = conflicted;
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
  window.__velvetBridgeQueueSync = { sync, queueFor, adopt, reconcile };
})();
