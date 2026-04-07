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

async function waitFor(predicate, { timeoutMs = 4000, intervalMs = 20 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  throw new Error('Timed out waiting for condition');
}

function createHarness() {
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

    throw new Error(`Unhandled fetch: ${method} ${parsedUrl.pathname}`);
  };

  return {
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
          Object.defineProperty(window, 'innerWidth', {
            configurable: true,
            writable: true,
            value: 390,
          });
          Object.defineProperty(window, 'outerWidth', {
            configurable: true,
            writable: true,
            value: 390,
          });
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

function primeAudio(dom, { paused = false, currentTime = 12, duration = 180, readyState = 4 } = {}) {
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

async function testAutoOpenOnlyBeforeDismiss() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    primeAudio(dom, { paused: true });
    const queue = [
      {
        id: 1,
        title: 'First Track',
        artist_name: 'Artist One',
        album_title: 'Album One',
        album_id: 11,
        duration: 180,
        format: 'FLAC',
        sample_rate: 44100,
        bit_depth: 16,
      },
      {
        id: 2,
        title: 'Second Track',
        artist_name: 'Artist Two',
        album_title: 'Album One',
        album_id: 11,
        duration: 200,
        format: 'FLAC',
        sample_rate: 96000,
        bit_depth: 24,
      },
    ];

    await dom.window.playTrack({ ...queue[0] }, queue, 0);
    await waitFor(() => dom.window.document.querySelector('#mobile-fullscreen-player')?.classList.contains('open'));

    dom.window.document.querySelector('#mobile-player-back').click();
    assert.equal(dom.window.document.querySelector('#mobile-fullscreen-player').classList.contains('open'), false);
    assert.equal(dom.window.eval('state.mobilePlayerDismissed'), true);

    await dom.window.playTrack({ ...queue[1] }, queue, 1);
    await new Promise(resolve => dom.window.setTimeout(resolve, 30));

    assert.equal(dom.window.document.querySelector('#mobile-fullscreen-player').classList.contains('open'), false);

    dom.window.document.querySelector('#player-art').click();
    await waitFor(() => dom.window.document.querySelector('#mobile-fullscreen-player')?.classList.contains('open'));
  } finally {
    dom.window.close();
  }
}

async function testBottomBarPauseDoesNotOpenFullscreen() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const audio = primeAudio(dom, { paused: false });
    dom.window.eval(`
      state.currentTrack = {
        id: 7,
        title: 'Pause Check',
        artist_name: 'Status Artist',
        album_title: 'Status Album',
        album_id: 77,
        duration: 240,
        format: 'FLAC',
        sample_rate: 96000,
        bit_depth: 24
      };
      state.playing = true;
      updatePlayerUI(state.currentTrack);
      updatePlayBtnUI();
    `);

    dom.window.document.querySelector('#btn-play').click();
    await new Promise(resolve => dom.window.setTimeout(resolve, 30));

    assert.equal(audio.paused, true);
    assert.equal(dom.window.eval('state.playing'), false);
    assert.equal(dom.window.document.querySelector('#mobile-fullscreen-player').classList.contains('open'), false);
  } finally {
    dom.window.close();
  }
}

async function main() {
  await testAutoOpenOnlyBeforeDismiss();
  await testBottomBarPauseDoesNotOpenFullscreen();
}

try {
  await main();
  console.log('frontend_mobile_player_behavior=OK');
  process.exit(0);
} catch (error) {
  console.error('frontend_mobile_player_behavior=FAILED');
  console.error(error);
  process.exit(1);
}
