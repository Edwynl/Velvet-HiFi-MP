// Unit tests for queue_sync.js against stand-ins for Velvet's page globals.
// Run: node remote_bridge/tests/queue_sync.test.mjs  (Node 18+, no packages)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const SOURCE = readFileSync(new URL('../queue_sync.js', import.meta.url), 'utf8');

// The bridge's /api/bridge endpoints, shared by every page of a test: one
// session, and update_queue's compare-and-swap on the revision.
class FakeBridge {
  constructor(session = null) {
    this.session = session;
    this.posts = [];
    this.beforePost = null; // runs before a POST is handled (another page's edit landing first)
  }
  get() {
    return this.session ? structuredClone(this.session) : { active: false };
  }
  post(body) {
    this.posts.push(body);
    if (this.beforePost) {
      const hook = this.beforePost;
      this.beforePost = null;
      hook(this);
    }
    const s = this.session;
    if (!s || body.track_id !== s.track_id || body.expected_revision !== s.revision) {
      return [409, { success: false, conflict: true, revision: s ? s.revision : null }];
    }
    this.edit({ ids: body.ids, queue_index: body.index, repeat: body.repeat });
    return [200, { success: true, revision: this.session.revision }];
  }
  edit(change) {
    const next = { ...this.session, ...change };
    if (JSON.stringify(next) !== JSON.stringify(this.session)) this.session = { ...next, revision: this.session.revision + 1 };
  }
}

function element() {
  return { style: { color: '' } };
}

function page({ queue = [5, 6, 7], index = 0, current = 5, casting = true, session = null, bridge = null,
  managementFetch = null } = {}) {
  const calls = [];
  const played = [];
  const zones = [];
  bridge = bridge || new FakeBridge(session);
  const state = {
    queue: queue.map(id => ({ id, title: `T${id}`, artist_name: 'A' })),
    queueIdx: index,
    currentTrack: current === null ? null : { id: current, title: `T${current}` },
    repeatMode: 'none',
    outputMode: casting ? 'cast' : 'browser',
    castDevice: casting ? { udn: 'uuid:EXN100', name: 'EXN100' } : null,
    castPending: false,
    castControlPending: null,
    castSeq: 0,
    zoneEpoch: 0,
  };
  if (casting && current !== null) state.currentTrack = state.queue.find(t => t.id === current) || state.currentTrack;
  const elements = { 'btn-repeat': element(), 'mobile-btn-repeat': element() };
  const respond = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  const context = {
    state,
    authState: { apiKey: 'k' },
    isCastZone: () => state.outputMode === 'cast' && Boolean(state.castDevice),
    playTrack: track => played.push(track.id),
    activateZone: zone => zones.push(zone),
    updatePlayerUI() {},
    updatePlayBtnUI() {},
    startCastPolling() { context.polling = true; },
    stopCastPolling() { context.polling = false; },
    showToast(message) { context.toasts.push(message); },
    toasts: [],
    location: { href: 'http://100.80.1.2:8877/', origin: 'http://100.80.1.2:8877' },
    document: { visibilityState: 'visible', addEventListener() {}, getElementById: id => elements[id] || null },
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
      if (path === '/api/bridge/session' || path.startsWith('/api/bridge/session/')) return respond(200, bridge.get());
      if (path.startsWith('/api/bridge/queue/')) return respond(...bridge.post(JSON.parse(init.body)));
      const track = path.match(/^\/api\/tracks\/(\d+)$/);
      if (track) return respond(200, { id: Number(track[1]), title: `Full ${track[1]}`, artist_name: 'Parent' });
      return respond(200, { success: true });
    },
  };
  if (managementFetch) context.managementFetch = (url, init) => managementFetch(context.fetch, url, init);
  context.window = context;
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  const casts = () => calls.filter(call => /^\/api\/cast\//.test(call.url));
  const posts = () => calls.filter(call => call.url.startsWith('/api/bridge/queue/'));
  const trackFetches = () => calls.filter(call => /^\/api\/tracks\/\d+$/.test(call.url)).map(call => Number(call.url.split('/').pop()));
  return { context, state, calls, casts, posts, trackFetches, played, zones, elements, bridge,
    api: context.__velvetBridgeQueueSync };
}

const settle = () => new Promise(resolve => setTimeout(resolve, 20));
const ids = state => state.queue.map(t => t.id);

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
  const all = Array.from({ length: 6000 }, (_, i) => i + 1);
  const { api } = page({ queue: all, index: 500, current: 501 });
  const queue = api.queueFor(501);
  assert.equal(queue.ids.length, 5000);
  assert.equal(queue.index, 0);
  assert.deepEqual([queue.ids[0], queue.ids[4999]], [501, 5500]);
});

const advanced = { active: true, udn: 'uuid:EXN100', name: 'EXN100', track_id: 6, queue_index: 1, ids: [5, 6, 7],
  repeat: 'none', revision: 3, advance_seq: 1, idle: false, state: 'PLAYING', position: 12, skipped: [], error: null };

test('the page shows an advance the bridge made, without casting', async () => {
  const { api, state, casts, played, posts, bridge } = page({ session: advanced });
  await api.sync();
  assert.equal(state.currentTrack.id, 6);
  assert.equal(state.queueIdx, 1);
  assert.equal(state.castConfirmedTrackId, 6);
  assert.equal(state.playing, true);
  assert.deepEqual(played, []);
  assert.deepEqual(casts(), []);
  assert.deepEqual(posts(), []); // following is not an edit
  state.queue.push({ id: 8 }); // later: our own queue edit is posted, not overridden
  await api.sync();
  assert.equal(state.currentTrack.id, 6);
  assert.deepEqual(JSON.parse(posts()[0].init.body).expected_revision, 3);
  assert.deepEqual(bridge.session.ids, [5, 6, 7, 8]);
});

test('no following while the user\'s own cast is unanswered', async () => {
  const { api, state } = page({ session: advanced });
  state.castPending = true;
  await api.sync();
  assert.equal(state.currentTrack.id, 5);
});

test('another phone changing the track is followed; an old revision is not', async () => {
  const { api, state, bridge } = page({ session: { ...advanced, revision: 3 } });
  await api.sync();
  assert.equal(state.currentTrack.id, 6);
  state.currentTrack = state.queue[2]; // this page then casts track 7 itself...
  state.queueIdx = 2;
  bridge.session = { ...advanced, track_id: 6, revision: 3 }; // ...and a stale answer arrives
  await api.sync();
  assert.equal(state.currentTrack.id, 7);
  bridge.session = { ...advanced, track_id: 5, queue_index: 0, revision: 9 }; // a newer change elsewhere
  await api.sync();
  assert.equal(state.currentTrack.id, 5);
});

test('a freshly loaded page reattaches to the playing session without casting', async () => {
  const { api, state, casts, context, posts } = page({ queue: [], index: -1, current: null, casting: false, session: advanced });
  await api.sync();
  await settle();
  assert.equal(state.outputMode, 'cast');
  assert.equal(state.castDevice.udn, 'uuid:EXN100');
  assert.deepEqual(ids(state), [5, 6, 7]);
  assert.equal(state.currentTrack.title, 'Full 6'); // details from the parent
  assert.equal(state.queue[2].title, 'Full 7'); // filled in the background
  assert.equal(state.castConfirmedTrackId, 6);
  assert.equal(context.polling, true);
  assert.deepEqual(casts(), []);
  await api.sync();
  assert.deepEqual(posts(), []); // and asks for no change afterwards
});

test('a reattached page keeps the session\'s repeat mode and never resets it', async () => {
  const { api, state, elements, posts, bridge } = page({ queue: [], index: -1, current: null, casting: false,
    session: { ...advanced, repeat: 'all' } });
  await api.sync();
  assert.equal(state.repeatMode, 'all');
  assert.equal(elements['btn-repeat'].style.color, 'var(--gold)');
  assert.equal(elements['mobile-btn-repeat'].style.color, 'var(--gold)');
  await api.sync();
  await api.sync(); // two more cycles
  assert.deepEqual(posts(), []);
  assert.equal(bridge.session.repeat, 'all');
  assert.equal(bridge.session.revision, 3);
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

test('queue changes are posted once per change, against the revision seen', async () => {
  const session = { ...advanced, track_id: 5, queue_index: 0, revision: 0 };
  const { api, state, posts, bridge } = page({ session });
  await api.sync();
  await api.sync();
  assert.equal(posts().length, 0); // nothing changed yet
  state.repeatMode = 'all';
  await api.sync();
  await api.sync();
  assert.equal(posts().length, 1);
  assert.deepEqual(JSON.parse(posts()[0].init.body),
    { track_id: 5, ids: [5, 6, 7], index: 0, repeat: 'all', expected_revision: 0 });
  assert.equal(posts()[0].init.headers['X-API-Key'], 'k');
  assert.equal(posts()[0].url, '/api/bridge/queue/uuid:EXN100'); // under /api: the admin cookie's path
  assert.equal(bridge.session.repeat, 'all');
});

test('nothing is sent when not casting and no session plays', async () => {
  const { api, calls } = page({ casting: false, session: { active: false } });
  await api.sync();
  assert.deepEqual(calls.map(call => call.url), []); // a page with a track never looks for one
});

// ── Review 6 / P1-3: two pages editing the same session ──

function twoPages(session = { ...advanced, track_id: 5, queue_index: 0, ids: [5, 6, 7, 8], revision: 4 }) {
  const bridge = new FakeBridge(session);
  const a = page({ queue: session.ids, index: 0, current: 5, bridge });
  const b = page({ queue: session.ids, index: 0, current: 5, bridge });
  return { bridge, a, b };
}

test('another page\'s edit of the upcoming tracks is taken in, and a later repeat toggle keeps it', async () => {
  const { bridge, a, b } = twoPages();
  await a.api.sync();
  await b.api.sync();
  a.state.queue.splice(2, 1); // A removes track 7 while track 5 plays on
  await a.api.sync();
  assert.deepEqual(bridge.session.ids, [5, 6, 8]);
  await b.api.sync(); // B keeps listening to track 5
  assert.deepEqual(ids(b.state), [5, 6, 8]);
  assert.equal(b.state.currentTrack.id, 5);
  b.state.repeatMode = 'all'; // B's unrelated toggle
  await b.api.sync();
  assert.deepEqual(bridge.session.ids, [5, 6, 8], 'the removed track came back');
  assert.equal(bridge.session.repeat, 'all');
  assert.deepEqual(b.posts().map(p => JSON.parse(p.init.body).ids), [[5, 6, 8]]);
});

test('an edit made before seeing another page\'s is merged with it, never written over it', async () => {
  const { bridge, a, b } = twoPages();
  await a.api.sync();
  await b.api.sync();
  a.state.queue.splice(2, 1);
  await a.api.sync();
  b.state.repeatMode = 'all'; // B toggles before its next look at the bridge
  await b.api.sync();
  assert.deepEqual(bridge.session.ids, [5, 6, 8]);
  assert.equal(bridge.session.repeat, 'all');
  assert.deepEqual(ids(b.state), [5, 6, 8]);
});

test('a change landing between a page\'s look and its post is refused (409), then merged', async () => {
  const { bridge, a, b } = twoPages();
  await a.api.sync();
  await b.api.sync();
  b.state.repeatMode = 'one';
  bridge.beforePost = server => server.edit({ ids: [5, 8, 6, 7] }); // A's reorder wins the race
  await b.api.sync();
  assert.deepEqual(bridge.session.ids, [5, 8, 6, 7]); // the stale post changed nothing
  assert.equal(bridge.session.repeat, 'none');
  await settle(); // the immediate re-sync
  assert.deepEqual(bridge.session.ids, [5, 8, 6, 7]);
  assert.equal(bridge.session.repeat, 'one');
  assert.deepEqual(ids(b.state), [5, 8, 6, 7]);
  assert.equal(a.state.repeatMode, 'none');
  await a.api.sync();
  assert.equal(a.state.repeatMode, 'one');
});

test('the same tracks edited on two pages: the bridge\'s version stands and the page says so', async () => {
  const { bridge, a, b } = twoPages();
  await a.api.sync();
  await b.api.sync();
  a.state.queue.reverse(); // [8, 7, 6, 5]
  b.state.queue.splice(1, 1); // [5, 7, 8]
  await a.api.sync();
  await b.api.sync();
  assert.deepEqual(bridge.session.ids, [8, 7, 6, 5]);
  assert.deepEqual(ids(b.state), [8, 7, 6, 5]);
  assert.equal(b.state.currentTrack.id, 5);
  assert.equal(b.posts().length, 0);
  assert.match(b.context.toasts.join(), /changed on another device/);
});

test('an edit is merged across the bridge\'s own advance', async () => {
  const { bridge, a } = twoPages();
  await a.api.sync();
  a.state.queue.push({ id: 9 }); // added, unsent...
  bridge.edit({ track_id: 6, queue_index: 1 }); // ...as the bridge moves on to track 6
  await a.api.sync();
  assert.equal(a.state.currentTrack.id, 6);
  assert.deepEqual(bridge.session.ids, [5, 6, 7, 8, 9]);
  assert.equal(bridge.session.track_id, 6);
});

test('a cancelled administrator prompt is not repeated for the same edit', async () => {
  let prompts = 0;
  const managementFetch = async (fetch, url, init) => {
    prompts += 1;
    return { ok: false, status: 403, adminCancelled: true, json: async () => ({ code: 'admin_password_required' }) };
  };
  const { api, state } = page({ session: { ...advanced, track_id: 5, queue_index: 0, revision: 0 }, managementFetch });
  await api.sync();
  state.repeatMode = 'all';
  await api.sync();
  await api.sync();
  assert.equal(prompts, 1);
  state.repeatMode = 'one'; // another edit: ask again
  await api.sync();
  assert.equal(prompts, 2);
});

// ── Review 7 / P1: another device's Stop is final on every page ──

test('a Stop from another device disarms this page\'s cast auto-next and polling, sending nothing', async () => {
  const { api, state, context, calls, bridge } = page({ session: advanced });
  await api.sync(); // following the session on track 6
  state.castAutoNextArmed = true;
  context.polling = true;
  const before = calls.length;
  bridge.session = null; // phone A stopped the EXN100: /api/bridge/session says {active: false}
  await api.sync();
  await api.sync();
  assert.equal(state.castAutoNextArmed, false);
  assert.equal(state.castPlaybackUnverified, true);
  assert.equal(state.playing, false);
  assert.equal(context.polling, false);
  assert.deepEqual(context.toasts, ['EXN100 stopped playing'], 'told once');
  assert.deepEqual(calls.slice(before).map(c => c.url), ['/api/bridge/session/uuid:EXN100', '/api/bridge/session/uuid:EXN100']);
});

test('an idle session (end of queue, Stop on the renderer) is shown stopped, then followed when it plays again', async () => {
  const { api, state, context, bridge } = page({ session: advanced });
  await api.sync();
  bridge.session = { ...advanced, idle: true, state: 'STOPPED' };
  await api.sync();
  assert.equal(state.castAutoNextArmed, false);
  assert.equal(context.polling, false);
  bridge.session = { ...advanced, idle: false, state: 'PLAYING' }; // Play pressed on the EXN100
  await api.sync();
  assert.equal(context.polling, true);
  assert.equal(state.castAutoNextArmed, true);
  assert.equal(state.castPlaybackUnverified, false);
  assert.equal(state.currentTrack.id, 6);
});

test('after a Stop, another device\'s new cast is followed again', async () => {
  const { api, state, context, bridge } = page({ session: advanced });
  await api.sync();
  bridge.session = null;
  await api.sync();
  bridge.session = { ...advanced, track_id: 7, queue_index: 2, revision: 12 };
  await api.sync();
  assert.equal(state.currentTrack.id, 7);
  assert.equal(context.polling, true);
});

test('a page that never saw its session play is not told it stopped', async () => {
  const { api, context } = page({ session: { active: false } }); // e.g. its first cast failed
  await api.sync();
  assert.deepEqual(context.toasts, []);
});

// ── Review 6 / P2: a reattached queue does not fetch every track ──

test('a reattached long queue loads the current track first and only those around it', async () => {
  const all = Array.from({ length: 5000 }, (_, i) => i + 1);
  const session = { ...advanced, ids: all, track_id: 2001, queue_index: 2000, revision: 7 };
  const { api, state, trackFetches } = page({ queue: [], index: -1, current: null, casting: false, session });
  await api.sync();
  assert.equal(trackFetches()[0], 2001);
  await settle();
  const fetched = trackFetches();
  assert.ok(fetched.length <= 1 + 5 + 40, `${fetched.length} track requests`);
  assert.ok(fetched.every(id => id >= 1996 && id <= 2041));
  assert.equal(state.queue[2040].title, 'Full 2041');
  assert.equal(state.queue[3000].placeholder, true);
});

test('loading stops when the page moves to another queue', async () => {
  const all = Array.from({ length: 200 }, (_, i) => i + 1);
  const session = { ...advanced, ids: all, track_id: 1, queue_index: 0, revision: 7 };
  const { api, state, trackFetches } = page({ queue: [], index: -1, current: null, casting: false, session });
  await api.sync();
  state.queue = [{ id: 999, title: 'Mine' }]; // the user plays something else right away
  await settle();
  assert.ok(trackFetches().length <= 1 + 4, `${trackFetches().length} track requests after leaving`);
});
