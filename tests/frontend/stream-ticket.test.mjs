import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync('static/index.html', 'utf8').replace(/\r\n/g, '\n');
const between = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));

assert.ok(!/[?&]api_key=/.test(html), 'No URL may carry the long-lived API key');

const calls = [];
let now = 1_700_000_000_000;
let nextTicket = 1;
const authState = { apiKey: 'long-lived-key' };
const context = vm.createContext({
  authState,
  state: { upsample: 'none', dspProfile: 3 },
  encodeURIComponent,
  JSON,
  Promise,
  Date: { now: () => now },
  setInterval() {},
  document: { visibilityState: 'visible', addEventListener() {} },
  async api(path, opts) {
    calls.push({ path, opts });
    return { ticket: `ticket-${nextTicket++}`, expires_at: Math.floor(now / 1000) + 4 * 3600 };
  },
});
vm.runInContext(between('function streamUrl(', '// DO NOT call initAudioAnalyser()'), context);
const run = code => vm.runInContext(code, context);

const plain = run('streamUrl(5)');
assert.ok(!plain.includes('long-lived-key') && !plain.includes('api_key'), plain);

// No usable ticket yet: nothing synchronous to hand out.
assert.equal(run('currentStreamTicket()'), '');

// Prefetch is deduplicated and yields a ticket usable without awaiting.
run('maintainStreamTicket(); maintainStreamTicket();');
assert.equal(await run('streamTicketRequest'), 'ticket-1');
assert.equal(calls.length, 1);
assert.equal(calls[0].path, '/stream-ticket');
assert.equal(calls[0].opts.method, 'POST');
assert.equal(calls[0].opts.body, undefined, 'The ticket is not tied to a track or its credentials');
assert.equal(run('currentStreamTicket()'), 'ticket-1');
assert.equal(run('streamUrl(5, undefined, currentStreamTicket())'),
  '/api/stream/5?upsample=none&dsp=3&ticket=ticket-1');

// Renewed in the background before it becomes unusable for a new track.
now += 2.5 * 3600 * 1000;
assert.equal(run('currentStreamTicket()'), 'ticket-1');
run('maintainStreamTicket()');
await run('streamTicketRequest');
assert.equal(run('currentStreamTicket()'), 'ticket-2');

// A changed API key (re-login / rotation) invalidates the cached ticket.
authState.apiKey = 'rotated-key';
assert.equal(run('currentStreamTicket()'), '');

// Unprotected local host: no ticket and no request.
authState.apiKey = '';
calls.length = 0;
run('maintainStreamTicket()');
assert.equal(await run('refreshStreamTicket()'), '');
assert.equal(calls.length, 0);

// Tap-to-play must not await the network when a prefetched ticket exists
// (mobile Safari drops the user-gesture chain across such awaits).
const playbackSource = between('async function startBrowserPlayback(', 'async function playTrack(');
const beforePlay = playbackSource.slice(0, playbackSource.indexOf('await audio.play()'));
const awaits = beforePlay.match(/await [\w.]+\(/g) || [];
assert.deepEqual(awaits, ['await refreshStreamTicket('],
  'Only the no-prefetched-ticket fallback may await before audio.play()');
assert.match(beforePlay, /currentStreamTicket\(\) \|\| await refreshStreamTicket\(\)/);

// Prefetch happens at startup and right after login / key changes.
assert.match(between("window.addEventListener('DOMContentLoaded'", "log('[VELVET] DOM ready"), /maintainStreamTicket\(\)/);
assert.match(between('async function submitLogin()', 'function coverUrl('), /maintainStreamTicket\(\)/);

// Casting sends only the track id; the server mints the renderer's ticketed URL.
const castCalls = [];
const castContext = vm.createContext({
  JSON,
  location: { origin: 'http://localhost:8765' },
  audio: { pause() {} },
  showToast() {},
  markExpectedBrowserPause() {},
  startCastPolling() {},
  streamUrl() { throw new Error('castTo must not build a credentialed URL'); },
  async api(path, opts) { castCalls.push({ path, opts }); return { success: true }; },
});
vm.runInContext(between('async function castTo(', 'async function toggleCastMenu('), castContext);
await vm.runInContext('castTo({ id: 5 }, { udn: "uuid:renderer", name: "Marantz" })', castContext);
assert.equal(castCalls[0].path, '/cast/uuid:renderer');
assert.deepEqual(JSON.parse(castCalls[0].opts.body), { track_id: 5 });

console.log('frontend_stream_ticket_check=OK');
