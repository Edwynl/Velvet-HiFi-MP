import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync('static/index.html', 'utf8');
const between = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));

assert.ok(!/[?&]api_key=/.test(html), 'No URL may carry the long-lived API key');

const calls = [];
const authState = { apiKey: 'long-lived-key' };
const context = vm.createContext({
  authState,
  state: { upsample: 'none', dspProfile: 3 },
  encodeURIComponent,
  JSON,
  async api(path, opts) {
    calls.push({ path, opts });
    return { ticket: '1700000000.sig-for-track-5', expires_at: 1700000000 };
  },
});
vm.runInContext(between('function streamUrl(', '// DO NOT call initAudioAnalyser()'), context);

const plain = vm.runInContext('streamUrl(5)', context);
assert.ok(!plain.includes('long-lived-key') && !plain.includes('api_key'), plain);

const ticket = await vm.runInContext('fetchStreamTicket(5)', context);
assert.equal(ticket, '1700000000.sig-for-track-5');
assert.equal(calls[0].path, '/stream-ticket');
assert.equal(calls[0].opts.method, 'POST');
assert.deepEqual(JSON.parse(calls[0].opts.body), { track_id: 5 });

const ticketed = vm.runInContext(`streamUrl(5, undefined, ${JSON.stringify(ticket)})`, context);
assert.equal(ticketed, '/api/stream/5?upsample=none&dsp=3&ticket=1700000000.sig-for-track-5');

authState.apiKey = '';
calls.length = 0;
assert.equal(await vm.runInContext('fetchStreamTicket(5)', context), '', 'Unprotected local host needs no ticket');
assert.equal(calls.length, 0);

const playbackSource = between('async function startBrowserPlayback(', 'async function playTrack(');
assert.match(playbackSource, /streamUrl\(track\.id, undefined, await fetchStreamTicket\(track\.id\)\)/,
  'Browser playback must request a per-track ticket before setting audio.src');

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
