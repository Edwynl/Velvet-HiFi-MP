// Unit tests for queue_sync.js against stand-ins for Velvet's page globals.
// Run: node remote_bridge/tests/queue_sync.test.mjs  (Node 18+, no packages)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const SOURCE = readFileSync(new URL('../queue_sync.js', import.meta.url), 'utf8');

function page({ queue = [5, 6, 7], index = 0, current = 5, casting = true, session = null } = {}) {
  const calls = [];
  const played = [];
  const zones = [];
  const state = {
    queue: queue.map(id => ({ id, title: `T${id}`, artist_name: 'A' })),
    queueIdx: index,
    currentTrack: current === null ? null : { id: current, title: `T${current}` },
    repeatMode: 'none',
    outputMode: casting ? 'cast' : 'browser',
    castDevice: casting ? { udn: 'uuid:EXN100' } : null,
    castPending: false,
    castControlPending: null,
    castSeq: 0,
    zoneEpoch: 0,
  };
  if (casting && current !== null) state.currentTrack = state.queue.find(t => t.id === current) || state.currentTrack;
  const context = {
    state,
    authState: { apiKey: 'k' },
    isCastZone: () => state.outputMode === 'cast' && Boolean(state.castDevice),
    playTrack: track => played.push(track.id),
    activateZone: zone => zones.push(zone),
    updatePlayerUI() {},
    updatePlayBtnUI() {},
    startCastPolling() { context.polling = true; },
    showToast(message) { context.toasts.push(message); },
    toasts: [],
    location: { href: 'http://100.80.1.2:8877/', origin: 'http://100.80.1.2:8877' },
    document: { visibilityState: 'visible', addEventListener() {}, getElementById: () => null },
    addEventListener() {},
    setInterval() {},
    setTimeout,
    URL,
    JSON,
    Number,
    Map,
    Promise,
    Array,
    console,
    fetch: async (url, init) => {
      calls.push({ url: String(url), init });
      const path = String(url);
      if (path === '/bridge/session' || path.startsWith('/bridge/session/')) {
        return { ok: true, json: async () => context.session };
      }
      const track = path.match(/^\/api\/tracks\/(\d+)$/);
      if (track) return { ok: true, json: async () => ({ id: Number(track[1]), title: `Full ${track[1]}`, artist_name: 'Parent' }) };
      return { ok: true, json: async () => ({ success: true }) };
    },
    session,
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  const casts = () => calls.filter(call => /^\/api\/cast\//.test(call.url));
  return { context, state, calls, casts, played, zones, api: context.__velvetBridgeQueueSync };
}

const settle = () => new Promise(resolve => setTimeout(resolve, 20));

test('a cast request carries the queue', async () => {
  const { context, calls } = page({ queue: [5, 6, 7], index: 1, current: 6 });
  await context.window.fetch('/api/cast/uuid:EXN100', { method: 'POST', body: JSON.stringify({ track_id: 6, request_id: 'r' }) });
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(body.bridge_queue, { ids: [5, 6, 7], index: 1, repeat: 'none' });
  assert.equal(body.request_id, 'r');
});

test('other requests are sent unchanged', async () => {
  const { context, calls } = page();
  const control = { method: 'POST', body: '{}' };
  await context.window.fetch('/api/cast/control/uuid:EXN100/Pause', control);
  await context.window.fetch('/api/cast/uuid:EXN100', { method: 'POST', body: 'not json' });
  await context.window.fetch('/api/tracks/5');
  assert.equal(calls[0].init, control);
  assert.equal(calls[1].init.body, 'not json');
  assert.equal(calls[2].init, undefined);
});

test('a cast of a track outside the queue carries no queue', async () => {
  const { context, calls } = page();
  await context.window.fetch('/api/cast/uuid:EXN100', { method: 'POST', body: JSON.stringify({ track_id: 99 }) });
  assert.equal(JSON.parse(calls[0].init.body).bridge_queue, undefined);
});

test('a huge queue is cut to 5000 entries from the current one', () => {
  const ids = Array.from({ length: 6000 }, (_, i) => i + 1);
  const { api } = page({ queue: ids, index: 500, current: 501 });
  const queue = api.queueFor(501);
  assert.equal(queue.ids.length, 5000);
  assert.equal(queue.index, 0);
  assert.deepEqual([queue.ids[0], queue.ids[4999]], [501, 5500]);
});

const advanced = { active: true, udn: 'uuid:EXN100', name: 'EXN100', track_id: 6, queue_index: 1, ids: [5, 6, 7],
  revision: 3, advance_seq: 1, idle: false, state: 'PLAYING', position: 12, skipped: [], error: null };

test('the page shows an advance the bridge made, without casting', async () => {
  const { api, state, casts, played } = page({ session: advanced });
  await api.sync();
  assert.equal(state.currentTrack.id, 6);
  assert.equal(state.queueIdx, 1);
  assert.equal(state.castConfirmedTrackId, 6);
  assert.equal(state.playing, true);
  assert.deepEqual(played, []);
  assert.deepEqual(casts(), []);
  state.queue.push({ id: 8 }); // later: our own queue edit is posted, not overridden
  await api.sync();
  assert.equal(state.currentTrack.id, 6);
});

test('no following while the user\'s own cast is unanswered', async () => {
  const { api, state } = page({ session: advanced });
  state.castPending = true;
  await api.sync();
  assert.equal(state.currentTrack.id, 5);
});

test('another phone changing the track is followed; an old revision is not', async () => {
  const { api, state, context } = page({ session: { ...advanced, revision: 3 } });
  await api.sync();
  assert.equal(state.currentTrack.id, 6);
  state.currentTrack = state.queue[2]; // this page then casts track 7 itself...
  state.queueIdx = 2;
  context.session = { ...advanced, track_id: 6, revision: 3 }; // ...and a stale answer arrives
  await api.sync();
  assert.equal(state.currentTrack.id, 7);
  context.session = { ...advanced, track_id: 5, queue_index: 0, revision: 9 }; // a newer change elsewhere
  await api.sync();
  assert.equal(state.currentTrack.id, 5);
});

test('a freshly loaded page reattaches to the playing session without casting', async () => {
  const { api, state, casts, context } = page({ queue: [], index: -1, current: null, casting: false, session: advanced });
  await api.sync();
  await settle();
  assert.equal(state.outputMode, 'cast');
  assert.equal(state.castDevice.udn, 'uuid:EXN100');
  assert.deepEqual(state.queue.map(t => t.id), [5, 6, 7]);
  assert.equal(state.currentTrack.title, 'Full 6'); // details from the parent
  assert.equal(state.queue[2].title, 'Full 7'); // filled in the background
  assert.equal(state.castConfirmedTrackId, 6);
  assert.equal(context.polling, true);
  assert.deepEqual(casts(), []);
});

test('a page the user has taken over is never reattached', async () => {
  const { api, state, context } = page({ queue: [], index: -1, current: null, casting: false, session: advanced });
  context.activateZone({ type: 'browser', id: 'browser' }); // they picked Local Web Player
  await api.sync();
  assert.equal(state.outputMode, 'browser');
  const local = page({ queue: [9], index: 0, current: 9, casting: false, session: advanced });
  await local.api.sync(); // playing something locally
  assert.equal(local.state.currentTrack.id, 9);
  assert.equal(local.state.outputMode, 'browser');
});

test('an idle or absent session is not reattached', async () => {
  for (const session of [{ active: false }, { ...advanced, idle: true }, null]) {
    const { api, state } = page({ queue: [], index: -1, current: null, casting: false, session });
    await api.sync();
    assert.equal(state.outputMode, 'browser');
  }
});

test('skips and a stopped queue are reported once', async () => {
  const session = { ...advanced, track_id: 5, queue_index: 0, revision: 0,
    skipped: [{ track_id: 6, error: 'Track not found' }] };
  const { api, context } = page({ session });
  await api.sync();
  await api.sync();
  assert.deepEqual(context.toasts, ['Skipped 1 unplayable track(s) in the queue']);
});

test('queue changes are posted once per change', async () => {
  const session = { ...advanced, track_id: 5, queue_index: 0, revision: 0 };
  const { api, state, calls } = page({ session });
  const posts = () => calls.filter(call => call.url.startsWith('/bridge/queue/'));
  await api.sync();
  await api.sync();
  assert.equal(posts().length, 1);
  assert.deepEqual(JSON.parse(posts()[0].init.body), { track_id: 5, ids: [5, 6, 7], index: 0, repeat: 'none' });
  assert.equal(posts()[0].init.headers['X-API-Key'], 'k');
  state.repeatMode = 'all';
  await api.sync();
  assert.equal(posts().length, 2);
  assert.equal(JSON.parse(posts()[1].init.body).repeat, 'all');
});

test('nothing is sent when not casting and no session plays', async () => {
  const { api, calls } = page({ casting: false, session: { active: false } });
  await api.sync();
  assert.deepEqual(calls.map(call => call.url), []); // a page with a track never looks for one
});
