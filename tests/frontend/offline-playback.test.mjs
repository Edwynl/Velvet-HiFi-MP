// Real index.html in jsdom with a fake window.VelvetOffline: downloaded
// tracks play from their blob: URL unless EQ/upsampling is on, and EQ users
// fall back to the local copy only when the server cannot be reached.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const HTML_PATH = path.join(process.cwd(), 'static', 'index.html');

function json(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: name => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
    async json() { return structuredClone(body); },
    async text() { return JSON.stringify(body); },
  };
}

async function waitFor(predicate, timeoutMs = 5000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Timed out waiting for condition');
}

function fakeOffline({ stored = [], warm = {} } = {}) {
  const fake = {
    calls: { init: 0, configure: [], warm: [], load: [] },
    loadResult: null,
    async init() { fake.calls.init++; },
    configure(options) { fake.calls.configure.push(options); },
    has: id => stored.includes(Number(id)),
    urlFor: id => warm[Number(id)] || '',
    async load(id) { fake.calls.load.push(Number(id)); return fake.loadResult ?? (stored.includes(Number(id)) ? `blob:local/${id}` : ''); },
    async warm(ids) { fake.calls.warm.push([...ids].map(Number)); },
  };
  return fake;
}

async function boot({ offline, playImpl, statusReachable = () => true }) {
  const seen = { play: 0, streamTicket: 0, status: 0 };
  const route = (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const { pathname } = new URL(url, 'http://localhost');
    if (pathname === '/api/security/status') {
      seen.status++;
      return statusReachable() ? Promise.resolve(json({ passwordless_listening: true }))
        : Promise.reject(new TypeError('Failed to fetch'));
    }
    if (pathname === '/api/stream-ticket' && method === 'POST') { seen.streamTicket++; return new Promise(() => {}); }
    if (pathname === '/api/stats') return Promise.resolve(json({ artists: 0, albums: 0, tracks: 0, duration: 0 }));
    if (pathname === '/api/scan/status') return Promise.resolve(json({ running: false }));
    if (pathname === '/api/dsp/profiles') return Promise.resolve(json([]));
    if (pathname === '/api/recently-added') return Promise.resolve(json([]));
    if (/\/status$/.test(pathname)) return Promise.resolve(json({ running: false }));
    if (/^\/api\/tracks\/\d+$/.test(pathname)) return Promise.resolve(json({ id: 7, title: 'T', duration: 240 }));
    return Promise.resolve(json({}));
  };
  const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8')).replace(/<link[^>]+>/g, '');
  const dom = new JSDOM(html, {
    url: 'http://localhost/#home',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window) {
      window.VelvetOffline = offline;
      window.fetch = route;
      window.confirm = () => true;
      window.alert = () => {};
      window.scrollTo = () => {};
      window.matchMedia = query => ({ media: query, matches: false, addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
      window.requestAnimationFrame = cb => window.setTimeout(() => cb(Date.now()), 0);
      window.cancelAnimationFrame = id => window.clearTimeout(id);
      window.MediaMetadata = class { constructor(data) { Object.assign(this, data); } };
      window.navigator.mediaSession = { metadata: null, playbackState: 'none', setActionHandler() {} };
      window.HTMLMediaElement.prototype.play = function play() { seen.play++; return (playImpl || resolvedPlay)(this, window); };
      window.HTMLMediaElement.prototype.pause = function pause() { this.dispatchEvent(new window.Event('pause')); };
      window.HTMLMediaElement.prototype.load = function load() {};
    },
  });
  await waitFor(() => dom.window.eval('state.view') === 'home');
  return { dom, seen, toasts: spyToasts(dom.window) };
}

function spyToasts(window) {
  const toasts = [];
  const original = window.showToast;
  window.showToast = (message, ...rest) => { toasts.push(String(message)); return original(message, ...rest); };
  return toasts;
}

const resolvedPlay = el => { el.dispatchEvent(new el.ownerDocument.defaultView.Event('playing')); return Promise.resolve(); };
const src = dom => dom.window.eval('audio.src');

// Startup loads the offline index and hands the module the ticket source.
{
  const offline = fakeOffline();
  const { dom } = await boot({ offline });
  assert.equal(offline.calls.init, 1);
  assert.equal(typeof offline.calls.configure[0]?.getTicket, 'function');
  dom.window.close();
}

// No EQ, downloaded and warm: the blob URL plays synchronously within the tap.
{
  const offline = fakeOffline({ stored: [7], warm: { 7: 'blob:local/7' } });
  const { dom, seen } = await boot({ offline });
  dom.window.eval("authState.apiKey = 'saved-key'"); // Even a protected server needs no ticket.
  const before = seen.play;
  const pending = dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.equal(seen.play, before + 1, 'Local play() runs synchronously within the tap');
  assert.equal(await pending, true);
  assert.equal(src(dom), 'blob:local/7');
  assert.equal(seen.streamTicket, 0, 'Local playback never asks for a stream ticket');
  assert.equal(dom.window.eval('browserStreamOffset'), 0);
  dom.window.close();
}

// EQ on: the downloaded track still streams so the EQ is heard.
{
  const offline = fakeOffline({ stored: [7], warm: { 7: 'blob:local/7' } });
  const { dom } = await boot({ offline });
  dom.window.eval("state.dspProfile = '3'");
  await dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.match(src(dom), /\/api\/stream\/7\?/);
  dom.window.eval("state.dspProfile = ''; state.upsample = '2x'");
  await dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.match(src(dom), /\/api\/stream\/7\?upsample=2x/, 'Upsampling follows the EQ rule');
  dom.window.close();
}

// Downloaded but not warm yet: the URL is loaded first; a refused play()
// asks for one more tap instead of failing silently.
{
  const offline = fakeOffline({ stored: [7] });
  const { dom, toasts } = await boot({
    offline,
    playImpl: (el, window) => Promise.reject(new window.DOMException('gesture required', 'NotAllowedError')),
  });
  assert.equal(await dom.window.startBrowserPlayback({ id: 7, title: 'T' }), false);
  assert.deepEqual(offline.calls.load, [7]);
  assert.equal(src(dom), 'blob:local/7');
  assert.ok(toasts.some(t => /Tap play again/.test(t)));
  dom.window.close();
}

// EQ on but the server is unreachable: the local copy plays without EQ, the
// toast says so once per track, and no DSP health check is scheduled.
{
  const offline = fakeOffline({ stored: [7], warm: { 7: 'blob:local/7' } });
  const { dom, toasts } = await boot({ offline });
  let healthChecks = 0;
  dom.window.scheduleDspStartupHealthCheck = () => { healthChecks++; };
  dom.window.eval("state.dspProfile = '3'; browserForceLocal = true");
  await dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  await dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.equal(src(dom), 'blob:local/7');
  assert.equal(toasts.filter(t => t === 'Offline copy, EQ off').length, 1);
  assert.equal(healthChecks, 0);
  dom.window.dispatchEvent(new dom.window.Event('online'));
  assert.equal(dom.window.eval('browserForceLocal'), false, 'Coming back online re-enables EQ streaming');
  dom.window.close();
}

// A media error while streaming with EQ: probe the server; unreachable means
// switch to the local copy, reachable leaves the existing recovery alone.
{
  let reachable = true;
  const offline = fakeOffline({ stored: [7], warm: { 7: 'blob:local/7' } });
  const { dom, seen } = await boot({ offline, statusReachable: () => reachable });
  dom.window.eval("state.dspProfile = '3'");
  await dom.window.playTrack({ id: 7, title: 'T', artist_name: 'A' }, [{ id: 7 }], 0);
  assert.match(src(dom), /\/api\/stream\/7\?/);
  const statusBefore = seen.status;
  dom.window.eval('audio').dispatchEvent(new dom.window.Event('error'));
  await waitFor(() => seen.status > statusBefore);
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(dom.window.eval('browserForceLocal'), false);
  assert.match(src(dom), /\/api\/stream\/7\?/);

  reachable = false;
  dom.window.eval('audio').dispatchEvent(new dom.window.Event('error'));
  await waitFor(() => src(dom) === 'blob:local/7');
  assert.equal(dom.window.eval('browserForceLocal'), true);
  dom.window.close();
}

// Starting a track warms it and the next one in the queue.
{
  const offline = fakeOffline({ stored: [7, 8] });
  const { dom } = await boot({ offline });
  await dom.window.playTrack({ id: 7, title: 'T', artist_name: 'A' }, [{ id: 7 }, { id: 8 }, { id: 9 }], 0);
  assert.deepEqual(offline.calls.warm.at(-1), [7, 8]);
  dom.window.close();
}

// Without the offline module (unsupported browser) nothing changes.
{
  const { dom } = await boot({ offline: undefined });
  await dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.match(src(dom), /\/api\/stream\/7\?/);
  dom.window.close();
}

console.log('frontend_offline_playback=OK');
