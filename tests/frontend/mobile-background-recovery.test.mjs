import { readFileSync } from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

import { JSDOM } from 'jsdom';

const ROOT = process.cwd();
const HTML_PATH = path.join(ROOT, 'static', 'index.html');

function makeJsonResponse(body, status = 200) {
  const payload = structuredClone(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        return String(name).toLowerCase() === 'content-type' ? 'application/json' : null;
      },
    },
    async json() {
      return structuredClone(payload);
    },
    async text() {
      return JSON.stringify(payload);
    },
  };
}

async function waitFor(predicate, { timeoutMs = 5000, intervalMs = 25 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  throw new Error('Timed out waiting for condition');
}

function createHarness() {
  let playCalls = 0;

  const route = (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const parsedUrl = new URL(url, 'http://localhost');

    if (parsedUrl.pathname === '/api/stats' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ artists: 0, albums: 0, tracks: 0, duration: 0 }));
    }

    if (parsedUrl.pathname === '/api/scan/status' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ running: false, progress: 0, total: 0, current_file: '' }));
    }

    if (parsedUrl.pathname === '/api/config' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({
        music_dir: 'C:/Music',
        libraries: [{ id: 1, path: 'C:/Music' }],
        port: 9876,
        data_dir: 'C:/VELVET_DATA',
      }));
    }

    if (parsedUrl.pathname === '/api/enrich/fingerprint/status' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ running: false, progress: 0, total: 0, current_file: '' }));
    }

    if (parsedUrl.pathname === '/api/enrich/biography/status' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ running: false, progress: 0, total: 0, current_file: '' }));
    }

    if (parsedUrl.pathname === '/api/enrich/bios/status' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ running: false, progress: 0, total: 0, current_artist: '' }));
    }

    if (parsedUrl.pathname === '/api/dsp/profiles' && method === 'GET') {
      return Promise.resolve(makeJsonResponse([]));
    }

    if (parsedUrl.pathname === '/api/recently-added' && method === 'GET') {
      return Promise.resolve(makeJsonResponse([]));
    }

    const trackMatch = parsedUrl.pathname.match(/^\/api\/tracks\/(\d+)$/);
    if (trackMatch && method === 'GET') {
      const trackId = Number(trackMatch[1]);
      return Promise.resolve(makeJsonResponse({
        id: trackId,
        title: trackId === 7 ? 'Recovery Track' : 'Other Track',
        artist_name: 'Recovery Artist',
        album_title: 'Recovery Album',
        album_id: 77,
        duration: 240,
        format: 'FLAC',
        sample_rate: 96000,
        bit_depth: 24,
      }));
    }

    throw new Error(`Unhandled fetch: ${method} ${parsedUrl.pathname}`);
  };

  return {
    getPlayCalls() {
      return playCalls;
    },
    async createDom() {
      const html = readFileSync(HTML_PATH, 'utf8').replace(/<link[^>]+>/g, '');
      const dom = new JSDOM(html, {
        url: 'http://localhost/#home',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        beforeParse(window) {
          window.fetch = (url, opts) => route(url, opts);
          window.confirm = () => true;
          window.alert = () => {};
          window.scrollTo = () => {};
          window.matchMedia = query => ({
            media: query,
            matches: query === '(pointer: coarse)',
            onchange: null,
            addListener() {},
            removeListener() {},
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() { return false; },
          });
          window.requestAnimationFrame = cb => window.setTimeout(() => cb(Date.now()), 0);
          window.cancelAnimationFrame = id => window.clearTimeout(id);
          window.MediaMetadata = class MediaMetadata {
            constructor(data) {
              Object.assign(this, data);
            }
          };
          window.navigator.mediaSession = {
            metadata: null,
            playbackState: 'none',
            setActionHandler() {},
          };
          window.HTMLMediaElement.prototype.play = function() {
            playCalls += 1;
            this.paused = false;
            this.dispatchEvent(new window.Event('playing'));
            return Promise.resolve();
          };
          window.HTMLMediaElement.prototype.pause = function() {
            this.paused = true;
            this.dispatchEvent(new window.Event('pause'));
          };
          window.HTMLMediaElement.prototype.load = function() {};
        },
      });
      await waitFor(() => dom.window.document.querySelector('#player-title'));
      return dom;
    },
  };
}

function setDocumentHidden(dom, hidden) {
  Object.defineProperty(dom.window.document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
  Object.defineProperty(dom.window.document, 'visibilityState', {
    configurable: true,
    get: () => (hidden ? 'hidden' : 'visible'),
  });
}

function primeAudio(dom, { paused = false, currentTime = 42, duration = 240, readyState = 4 } = {}) {
  const audio = dom.window.eval('audio');
  Object.defineProperty(audio, 'paused', {
    configurable: true,
    writable: true,
    value: paused,
  });
  Object.defineProperty(audio, 'currentTime', {
    configurable: true,
    writable: true,
    value: currentTime,
  });
  Object.defineProperty(audio, 'duration', {
    configurable: true,
    writable: true,
    value: duration,
  });
  Object.defineProperty(audio, 'readyState', {
    configurable: true,
    writable: true,
    value: readyState,
  });
  return audio;
}

async function testRecoveryAfterWaiting() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const audio = primeAudio(dom, { paused: false, currentTime: 42 });
    setDocumentHidden(dom, true);
    dom.window.eval(`
      state.outputMode = 'browser';
      state.currentTrack = {
        id: 7,
        title: 'Recovery Track',
        artist_name: 'Recovery Artist',
        album_title: 'Recovery Album',
        album_id: 77,
        duration: 240,
        format: 'FLAC',
        sample_rate: 96000,
        bit_depth: 24
      };
      state.playing = true;
      updatePlayBtnUI();
    `);

    audio.dispatchEvent(new dom.window.Event('waiting'));

    await waitFor(() => harness.getPlayCalls() >= 1);
    assert.match(audio.src, /\/api\/stream\/7\?upsample=none$/);
    assert.equal(audio.currentTime, 42);
    assert.equal(dom.window.eval('state.playing'), true);
  } finally {
    dom.window.close();
  }
}

async function testManualPauseDoesNotAutoRecover() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    primeAudio(dom, { paused: false, currentTime: 18 });
    setDocumentHidden(dom, false);
    dom.window.eval(`
      state.outputMode = 'browser';
      state.currentTrack = {
        id: 7,
        title: 'Recovery Track',
        artist_name: 'Recovery Artist',
        album_title: 'Recovery Album',
        album_id: 77,
        duration: 240,
        format: 'FLAC',
        sample_rate: 96000,
        bit_depth: 24
      };
      state.playing = true;
      updatePlayBtnUI();
    `);

    dom.window.document.querySelector('#btn-play').click();
    await new Promise(resolve => setTimeout(resolve, 1200));

    assert.equal(harness.getPlayCalls(), 0);
    assert.equal(dom.window.eval('state.playing'), false);
  } finally {
    dom.window.close();
  }
}

try {
  await testRecoveryAfterWaiting();
  await testManualPauseDoesNotAutoRecover();
  console.log('frontend_mobile_background_recovery_check=OK');
  process.exit(0);
} catch (error) {
  console.error('frontend_mobile_background_recovery_check=FAILED');
  console.error(error);
  process.exit(1);
}
