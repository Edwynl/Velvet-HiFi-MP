import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync('static/index.html', 'utf8').replace(/\r\n/g, '\n');
const between = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));
const ticketSource = between('function streamUrl(', '// DO NOT call initAudioAnalyser()');

assert.ok(!/[?&]api_key=/.test(html), 'No URL may carry the long-lived API key');

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function makeContext({ apiKey = 'key-A', api } = {}) {
  const clock = { now: 1_700_000_000_000 };
  const authState = { apiKey };
  const context = vm.createContext({
    authState,
    state: { upsample: 'none', dspProfile: 3 },
    encodeURIComponent,
    JSON,
    Promise,
    Date: { now: () => clock.now },
    setInterval() {},
    document: { visibilityState: 'visible', addEventListener() {} },
    api,
  });
  vm.runInContext(ticketSource, context);
  return { context, authState, clock, run: code => vm.runInContext(code, context) };
}

const hours = h => Math.floor(1_700_000_000_000 / 1000) + h * 3600;

// 1. URLs never carry the key; a delivered ticket is usable synchronously.
{
  const { run } = makeContext({ api: async () => { throw new Error('no network expected'); } });
  const plain = run('streamUrl(5)');
  assert.ok(!plain.includes('key-A') && !plain.includes('api_key'), plain);
  assert.equal(run('currentStreamTicket()'), '');
  run(`adoptStreamTicket({ stream_ticket: 'T-login', stream_ticket_expires_at: ${hours(4)} })`);
  assert.equal(run('currentStreamTicket()'), 'T-login');
  assert.equal(run('streamUrl(5, undefined, currentStreamTicket())'),
    '/api/stream/5?upsample=none&dsp=3&ticket=T-login');
}

// 2. Login: the ticket is in memory before render() makes anything tappable,
//    even while a background /stream-ticket request never resolves.
{
  const hung = deferred();
  const calls = [];
  const { context, run } = makeContext({ apiKey: '', api: () => { calls.push('stream-ticket'); return hung.promise; } });
  let ticketAtRender = null;
  Object.assign(context, {
    document: { getElementById(id) { return id === 'login-password' ? { value: 'pw' } : { remove() {} }; },
                visibilityState: 'visible', addEventListener() {} },
    localStorage: { setItem() {} },
    alert(message) { throw new Error(message); },
    async loadDspProfiles() {},
    async render() { ticketAtRender = run('currentStreamTicket()'); },
    async fetch(url) {
      if (url === '/api/stats') return { ok: true };
      return { ok: true, async json() {
        return { success: true, api_key: 'key-A', stream_ticket: 'T-login', stream_ticket_expires_at: hours(4) };
      } };
    },
  });
  vm.runInContext(between('async function submitLogin()', 'function coverUrl('), context);
  await run('submitLogin()');
  assert.equal(ticketAtRender, 'T-login', 'Ticket must be ready before the view renders');
  assert.equal(calls.length, 0, 'Login needs no separate ticket request');
}

// 3. Cold start: the startup session refresh (which delivers the ticket) is
//    awaited before the first navigation renders tappable tracks.
{
  const startAt = html.indexOf("window.addEventListener('DOMContentLoaded'");
  const startup = html.slice(startAt, html.indexOf("api('/scan/status')", startAt));
  const adopt = startup.indexOf("adoptStreamTicket(await api('/security/login'");
  const firstNavigation = startup.indexOf('await navigate(');
  assert.ok(adopt >= 0 && firstNavigation > adopt, 'Startup must adopt the ticket before navigating');
}

// 4. Key rotation while an old-key request is in flight: the stale response is
//    discarded, the new key gets its own request, and the rotation ticket wins.
{
  const pending = [];
  const { authState, run } = makeContext({ api: () => { const d = deferred(); pending.push(d); return d.promise; } });
  const oldRequest = run('refreshStreamTicket()');
  assert.equal(pending.length, 1);
  authState.apiKey = 'key-B';
  run(`adoptStreamTicket({ stream_ticket: 'T-rotated', stream_ticket_expires_at: ${hours(4)} })`);
  const newRequest = run('refreshStreamTicket()');
  assert.equal(pending.length, 2, 'A new key must not reuse the old in-flight request');
  pending[0].resolve({ ticket: 'T-stale', expires_at: hours(4) });
  assert.equal(await oldRequest, '');
  assert.equal(run('currentStreamTicket()'), 'T-rotated', 'Stale response must not overwrite');
  pending[1].resolve({ ticket: 'T-renewed', expires_at: hours(4) });
  assert.equal(await newRequest, 'T-renewed');
  assert.equal(run('currentStreamTicket()'), 'T-renewed');
  authState.apiKey = 'key-C';
  assert.equal(run('currentStreamTicket()'), '', 'A ticket cached for another key is never used');
}

// 5. Fetch failure: no throw, no cached ticket, and the next attempt retries.
{
  let attempts = 0;
  const { run } = makeContext({ api: async () => { attempts += 1; if (attempts === 1) throw new Error('offline'); return { ticket: 'T-retry', expires_at: hours(4) }; } });
  assert.equal(await run('refreshStreamTicket()'), '');
  assert.equal(run('streamTicketRequest'), null);
  assert.equal(await run('refreshStreamTicket()'), 'T-retry');
  assert.equal(attempts, 2);
}

// 6. Background renewal before the ticket stops being usable for a new track.
{
  let n = 0;
  const { run, clock } = makeContext({ api: async () => ({ ticket: `T-${++n}`, expires_at: Math.floor(clock.now / 1000) + 4 * 3600 }) });
  run(`adoptStreamTicket({ stream_ticket: 'T-0', stream_ticket_expires_at: ${hours(4)} })`);
  run('maintainStreamTicket()');
  assert.equal(n, 0, 'A fresh ticket is not renewed');
  clock.now += 2.5 * 3600 * 1000;
  assert.equal(run('currentStreamTicket()'), 'T-0');
  run('maintainStreamTicket()');
  await run('streamTicketRequest.promise');
  assert.equal(run('currentStreamTicket()'), 'T-1');
}

// 7. Unprotected local host: no ticket and no request.
{
  let called = false;
  const { run } = makeContext({ apiKey: '', api: async () => { called = true; return {}; } });
  run('maintainStreamTicket()');
  assert.equal(await run('refreshStreamTicket()'), '');
  assert.equal(called, false);
}

// 8. Casting sends only the track id (plus the zone's DSP preset); the
// server mints the renderer's URL, ticketed, direct or DSP-transcoded.
{
  const castCalls = [];
  const castContext = vm.createContext({
    JSON,
    state: { dspProfile: '2', dspProfiles: [] },
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
  assert.deepEqual(JSON.parse(castCalls[0].opts.body), { track_id: 5, dsp_profile_id: 2 });
}

console.log('frontend_stream_ticket_check=OK');
