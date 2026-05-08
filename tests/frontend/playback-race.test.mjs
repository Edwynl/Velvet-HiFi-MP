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

    const trackMatch = parsedUrl.pathname.match(/^\/api\/tracks\/(\d+)$/);
    if (trackMatch && method === 'GET') {
      const trackId = Number(trackMatch[1]);
      const payload = trackId === 1
        ? {
            id: 1,
            title: 'Slow Track',
            artist_name: 'Artist One',
            album_title: 'Album One',
            album_id: 11,
            duration: 180,
            format: 'FLAC',
            sample_rate: 44100,
            bit_depth: 16,
          }
        : {
            id: 2,
            title: 'Fast Track',
            artist_name: 'Artist Two',
            album_title: 'Album Two',
            album_id: 22,
            duration: 200,
            format: 'FLAC',
            sample_rate: 96000,
            bit_depth: 24,
          };
      const delayMs = trackId === 1 ? 60 : 5;
      return new Promise(resolve => setTimeout(() => resolve(makeJsonResponse(payload)), delayMs));
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
          window.matchMedia = query => ({
            media: query,
            matches: false,
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
          window.HTMLMediaElement.prototype.play = function() { return Promise.resolve(); };
          window.HTMLMediaElement.prototype.pause = function() {};
          window.HTMLMediaElement.prototype.load = function() {};
        },
      });
      await waitFor(() => dom.window.document.querySelector('#player-title'));
      return dom;
    },
  };
}

async function main() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const queue = [
      { id: 1, title: 'Slow Track' },
      { id: 2, title: 'Fast Track' },
    ];

    const firstPlay = dom.window.playTrack({ ...queue[0] }, queue, 0);
    const secondPlay = new Promise(resolve => {
      dom.window.setTimeout(() => resolve(dom.window.playTrack({ ...queue[1] }, queue, 1)), 1);
    }).then(promise => promise);

    await Promise.allSettled([firstPlay, secondPlay]);
    await waitFor(() => dom.window.document.querySelector('#player-title')?.textContent?.trim() === 'Fast Track');
    await waitFor(() => dom.window.document.querySelector('#player-artist')?.textContent?.trim() === 'Artist Two');

    assert.equal(dom.window.document.querySelector('#player-title').textContent.trim(), 'Fast Track');
    assert.equal(dom.window.document.querySelector('#player-artist').textContent.trim(), 'Artist Two');
    assert.match(dom.window.eval('audio.src'), /\/api\/stream\/2\?upsample=none$/);
  } finally {
    dom.window.close();
  }
}

try {
  await main();
  console.log('frontend_playback_race_check=OK');
  process.exit(0);
} catch (error) {
  console.error('frontend_playback_race_check=FAILED');
  console.error(error);
  process.exit(1);
}
