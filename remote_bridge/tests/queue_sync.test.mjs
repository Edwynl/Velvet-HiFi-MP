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
  const state = {
    queue: queue.map(id => ({ id, title: `T${id}` })),
    queueIdx: index,
    currentTrack: current === null ? null : { id: current },
    repeatMode: 'none',
    castDevice: casting ? { udn: 'uuid:EXN100' } : null,
    castPending: false,
    castControlPending: null,
  };
  const context = {
    state,
    authState: { apiKey: 'k' },
    isCastZone: () => Boolean(state.castDevice),
    playTrack: track => played.push(track.id),
    location: { href: 'http://100.80.1.2:8877/', origin: 'http://100.80.1.2:8877' },
    document: { visibilityState: 'visible', addEventListener() {} },
    setInterval() {},
    URL,
    JSON,
    Number,
    console,
    fetch: async (url, init) => {
      calls.push({ url: String(url), init });
      if (String(url).startsWith('/bridge/session/')) {
        return { ok: true, json: async () => context.session };
      }
      return { ok: true, json: async () => ({ success: true }) };
    },
    session,
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  return { context, state, calls, played, api: context.__velvetBridgeQueueSync };
}

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

test('the page follows an advance the bridge made, once', async () => {
  const session = { active: true, track_id: 6, queue_index: 1, advance_seq: 3 };
  const { api, state, played } = page({ session });
  await api.sync();
  assert.deepEqual(played, [6]);
  assert.equal(state.queueIdx, 1);
  state.currentTrack = { id: 6 };
  await api.sync();
  assert.deepEqual(played, [6]);
});

test('no following while the user\'s own cast is unanswered', async () => {
  const session = { active: true, track_id: 6, queue_index: 1, advance_seq: 3 };
  const { api, state, played } = page({ session });
  state.castPending = true;
  await api.sync();
  assert.deepEqual(played, []);
});

test('queue changes are posted once per change', async () => {
  const session = { active: true, track_id: 5, queue_index: 0, advance_seq: 0 };
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

test('nothing is sent when not casting', async () => {
  const { api, calls } = page({ casting: false });
  await api.sync();
  assert.equal(calls.length, 0);
});
