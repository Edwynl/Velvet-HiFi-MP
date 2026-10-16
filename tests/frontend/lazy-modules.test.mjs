// Production-shaped shell: Settings/Remote are NOT inlined. The eager shell
// must never call into them before they load, and opening Settings must load
// the real module through the on-demand <script> path.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM, ResourceLoader, VirtualConsole } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const requests = [];
const scriptLoads = [];
let scanPolls = 0;
const response = data => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  async json() { return structuredClone(data); },
  async text() { return JSON.stringify(data); },
});
const request = async url => {
  const u = new URL(url, 'http://localhost');
  requests.push(u);
  if (u.pathname === '/api/scan/status') {
    scanPolls += 1;
    // Startup sees a running scan, then the poll observes completion.
    return response(scanPolls <= 2
      ? { running: true, progress: 1, total: 4 }
      : { running: false, albums_added: 2, tracks_added: 9 });
  }
  if (u.pathname === '/api/stats') return response({ albums: 2, artists: 1, tracks: 9 });
  if (u.pathname === '/api/security/status') return response({ enabled: false });
  if (u.pathname === '/api/dsp/profiles') return response([]);
  if (u.pathname === '/api/albums/page') return response({ albums: [], total: 0, genres: [], decades: [] });
  if (u.pathname === '/api/config') return response({ music_dirs: [], libraries: [] });
  return response({});
};

class StaticModuleLoader extends ResourceLoader {
  fetch(url) {
    const { pathname } = new URL(url);
    const match = pathname.match(/^\/static\/js\/(settings-view|remote-settings)\.js$/);
    if (!match) return Promise.resolve(Buffer.from(''));
    scriptLoads.push(match[1]);
    return Promise.resolve(readFileSync(`static/js/${match[1]}.js`));
  }
}

const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', err => errors.push(String(err?.message || err)));
virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));

const html = inlineFrontendModules(readFileSync('static/index.html', 'utf8'), { includeLazy: false })
  .replace(/<link[^>]+>/g, '');
assert.ok(!/<script src="\/static\/js\/(settings-view|remote-settings)\.js">/.test(html),
  'Settings/Remote are not part of the eager shell');

const dom = new JSDOM(html, {
  url: 'http://localhost/#albums', runScripts: 'dangerously', pretendToBeVisual: true,
  resources: new StaticModuleLoader(), virtualConsole,
  beforeParse(window) {
    window.fetch = request;
    window.alert = () => {};
    window.confirm = () => true;
    window.scrollTo = () => {};
    window.matchMedia = q => ({
      media: q, matches: false, addListener() {}, removeListener() {},
      addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
    });
    window.requestAnimationFrame = cb => window.setTimeout(cb, 0);
    window.cancelAnimationFrame = id => window.clearTimeout(id);
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
    window.HTMLMediaElement.prototype.load = () => {};
    window.MediaMetadata = class {};
    window.navigator.mediaSession = { setActionHandler() {}, metadata: null };
    window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)));
  },
});
const wait = async (predicate, label) => {
  for (let i = 0; i < 300; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Timed out: ' + label + '\n' + errors.join('\n'));
};

// A scan that is already running when the phone opens the app keeps polling
// until it completes, even though the Settings module has not been loaded.
await wait(() => scanPolls >= 3, 'startup scan polling reaches completion');
await wait(() => requests.filter(u => u.pathname === '/api/stats').length >= 2,
  'stats refresh after the scan completes');
assert.deepEqual(scriptLoads, [], 'Settings/Remote are not downloaded on first load');
assert.deepEqual(errors.filter(e => /updateScanStatusPanel|ReferenceError/.test(e)), []);

// Opening Settings downloads its module once and renders the real view.
dom.window.eval("navigate('settings')");
await wait(() => scriptLoads.includes('settings-view'), 'settings module download');
await wait(() => dom.window.document.getElementById('scan-status-panel'), 'settings view render');
assert.deepEqual(scriptLoads, ['settings-view']);
assert.deepEqual(errors.filter(e => /ReferenceError|is not defined/.test(e)), []);
console.log('frontend_lazy_modules_check=OK');
dom.window.close();
