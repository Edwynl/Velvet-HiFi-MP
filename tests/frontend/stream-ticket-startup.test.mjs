// Real index.html in jsdom: a cold start with a saved API key must leave the
// first tap-to-play with a usable stream ticket, i.e. audio.play() is called
// synchronously within the tap even though /api/stream-ticket never answers.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';

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

async function bootWithSavedKey({ loginBody, streamTicket, playImpl }) {
  const seen = { login: 0, streamTicket: 0, play: 0 };
  const route = (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const { pathname } = new URL(url, 'http://localhost');
    if (pathname === '/api/security/login' && method === 'POST') { seen.login++; return Promise.resolve(json(loginBody)); }
    if (pathname === '/api/stream-ticket' && method === 'POST') { seen.streamTicket++; return streamTicket(); }
    if (pathname === '/api/stats') return Promise.resolve(json({ artists: 0, albums: 0, tracks: 0, duration: 0 }));
    if (pathname === '/api/scan/status') return Promise.resolve(json({ running: false }));
    if (pathname === '/api/dsp/profiles') return Promise.resolve(json([]));
    if (pathname === '/api/recently-added') return Promise.resolve(json([]));
    if (pathname === '/api/config') return Promise.resolve(json({ music_dir: 'C:/Music', libraries: [] }));
    if (/\/status$/.test(pathname)) return Promise.resolve(json({ running: false }));
    if (/^\/api\/tracks\/\d+$/.test(pathname)) return Promise.resolve(json({ id: 7, title: 'T', duration: 240 }));
    return Promise.resolve(json({}));
  };

  const html = readFileSync(HTML_PATH, 'utf8').replace(/<link[^>]+>/g, '');
  const dom = new JSDOM(html, {
    url: 'http://localhost/#home',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window) {
      window.localStorage.setItem('velvet_api_key', 'saved-key');
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
      window.HTMLMediaElement.prototype.play = function play() { seen.play++; return playImpl(this, window); };
      window.HTMLMediaElement.prototype.pause = function pause() { this.dispatchEvent(new window.Event('pause')); };
      window.HTMLMediaElement.prototype.load = function load() {};
    },
  });
  // Startup has finished once the first navigation rendered the home view.
  await waitFor(() => seen.login === 1 && dom.window.eval('state.view') === 'home');
  return { dom, seen };
}

const hung = () => new Promise(() => {});
const resolvedPlay = el => { el.dispatchEvent(new el.ownerDocument.defaultView.Event('playing')); return Promise.resolve(); };

// Cold start: the session refresh delivered the ticket, so the first tap calls
// play() synchronously while the background ticket endpoint hangs.
{
  const { dom, seen } = await bootWithSavedKey({
    loginBody: { success: true, api_key: 'saved-key', stream_ticket: 'T-boot',
                 stream_ticket_expires_at: Math.floor(Date.now() / 1000) + 4 * 3600 },
    streamTicket: hung,
    playImpl: resolvedPlay,
  });
  const before = seen.play;
  const pending = dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.equal(seen.play, before + 1, 'audio.play() must run synchronously within the tap');
  assert.equal(await pending, true);
  const src = dom.window.eval('audio.src');
  assert.match(src, /ticket=T-boot/);
  assert.ok(!src.includes('saved-key'), 'The API key must never appear in the media URL');
  assert.equal(seen.streamTicket, 0, 'No ticket round trip was needed');
  dom.window.close();
}

// Fallback (ticket unusable, e.g. the page slept past its expiry): the tap has
// to wait for a ticket; if the browser then refuses play() for lack of a
// gesture, the user is told to tap again instead of failing silently.
{
  let releaseTicket;
  const gate = new Promise(resolve => { releaseTicket = resolve; });
  const { dom, seen } = await bootWithSavedKey({
    loginBody: { success: true, api_key: 'saved-key' },   // no ticket delivered
    streamTicket: () => gate.then(() => json({ ticket: 'T-late', expires_at: Math.floor(Date.now() / 1000) + 4 * 3600 })),
    playImpl: (el, window) => Promise.reject(new window.DOMException('gesture required', 'NotAllowedError')),
  });
  const before = seen.play;
  const pending = dom.window.startBrowserPlayback({ id: 7, title: 'T' });
  assert.equal(seen.play, before, 'Without a usable ticket play() cannot run synchronously');
  releaseTicket();
  assert.equal(await pending, false);
  assert.ok(seen.streamTicket >= 1);
  assert.match(dom.window.eval('audio.src'), /ticket=T-late/);
  assert.match(dom.window.document.body.textContent, /Tap play again/);
  dom.window.close();
}

console.log('frontend_stream_ticket_startup_check=OK');
