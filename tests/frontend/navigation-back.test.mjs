import { readFileSync } from 'node:fs';
import { inlineFrontendModules } from './inline-modules.mjs';
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

function createHarness({ initialHash = '#home' } = {}) {
  const fetchLog = [];
  const album = {
    id: 11,
    title: 'Back Test Album',
    artist_name: 'Back Test Artist',
    year: 2024,
    genre: 'Classical',
    is_hires: false,
    is_favorite: false,
    tracks: [
      {
        id: 101,
        title: 'Opening',
        artist_name: 'Back Test Artist',
        album_title: 'Back Test Album',
        album_id: 11,
        duration: 180,
        format: 'FLAC',
        sample_rate: 44100,
        bit_depth: 16,
      },
    ],
  };

  const albumList = [
    {
      id: 11,
      title: 'Back Test Album',
      artist_name: 'Back Test Artist',
      year: 2024,
      genre: 'Classical',
      updated_at: null,
      is_hires: false,
      is_favorite: false,
    },
  ];

  const genreAlbums = {
    Rock: [
      { ...albumList[0], id: 21, title: 'Rock Album', genre: 'Rock' },
    ],
    Jazz: [
      { ...albumList[0], id: 22, title: 'Jazz Album', genre: 'Jazz' },
    ],
  };

  const searchResults = {
    alpha: {
      tracks: [
        {
          id: 101,
          title: 'Alpha Track',
          artist_name: 'Search Artist',
          album_title: 'Search Album',
          album_id: 11,
          duration: 200,
          format: 'FLAC',
          sample_rate: 44100,
          bit_depth: 16,
        },
      ],
    },
  };

  const route = (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    const parsedUrl = new URL(url, 'http://localhost');

    if (parsedUrl.pathname === '/api/stats' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ artists: 1, albums: 3, tracks: 5, duration: 3 }));
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

    if (parsedUrl.pathname === '/api/albums/11' && method === 'GET') {
      return Promise.resolve(makeJsonResponse(album));
    }

    if (parsedUrl.pathname === '/api/albums/11/credits' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({
        composers: [],
        works: [],
        performers: [],
        conductors: [],
        ensembles: [],
      }));
    }

    if (parsedUrl.pathname === '/api/albums' && method === 'GET') {
      return Promise.resolve(makeJsonResponse(albumList));
    }
    if (parsedUrl.pathname === '/api/albums/page' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({
        albums: albumList, total: albumList.length,
        genres: ['Classical', 'Jazz', 'Rock'], decades: ['2020s'],
        limit: 60, offset: 0,
      }));
    }

    if (parsedUrl.pathname === '/api/artists' && method === 'GET') {
      return Promise.resolve(makeJsonResponse([
        { id: 7, name: 'Back Test Artist', album_count: 1 },
      ]));
    }
    if (parsedUrl.pathname === '/api/artists/page' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({
        artists: [{ id: 7, name: 'Back Test Artist', album_count: 1 }],
        total: 1, limit: 60, offset: 0,
      }));
    }

    if (parsedUrl.pathname === '/api/genres' && method === 'GET') {
      return Promise.resolve(makeJsonResponse([
        { genre: 'Rock', count: 1 },
        { genre: 'Jazz', count: 1 },
      ]));
    }

    if (parsedUrl.pathname === '/api/genres/Rock/albums' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ albums: genreAlbums.Rock }));
    }

    if (parsedUrl.pathname === '/api/genres/Jazz/albums' && method === 'GET') {
      return Promise.resolve(makeJsonResponse({ albums: genreAlbums.Jazz }));
    }

    if (parsedUrl.pathname === '/api/albums/11/favorite' && method === 'POST') {
      return Promise.resolve(makeJsonResponse({ success: true, is_favorite: true }));
    }

    if (parsedUrl.pathname === '/api/search' && method === 'GET') {
      const query = parsedUrl.searchParams.get('q') || '';
      return Promise.resolve(makeJsonResponse(searchResults[query] || { tracks: [] }));
    }

    throw new Error(`Unhandled fetch: ${method} ${parsedUrl.pathname}`);
  };

  return {
    fetchLog,
    async createDom() {
      const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8')).replace(/<link[^>]+>/g, '');
      const dom = new JSDOM(html, {
        url: `http://localhost/${initialHash}`,
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        beforeParse(window) {
          window.fetch = (url, opts) => {
            fetchLog.push(`${(opts?.method || 'GET').toUpperCase()} ${new URL(url, 'http://localhost').pathname}`);
            return route(url, opts);
          };
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

async function testAlbumBackFallsBackWhenHistoryBackDoesNothing() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    await dom.window.navigate('album', { id: 11 });
    await waitFor(() => dom.window.document.querySelector('.back-btn'));

    let backCalls = 0;
    dom.window.history.back = () => {
      backCalls += 1;
    };

    dom.window.document.querySelector('.back-btn').click();

    await waitFor(() => dom.window.location.hash === '#albums', { timeoutMs: 2000 });

    assert.equal(backCalls, 1);
    assert.equal(dom.window.eval('state.view'), 'albums');
    assert.equal(dom.window.document.querySelector('#albums-grid') !== null, true);
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

function albumListFetches(harness) {
  return harness.fetchLog.filter(entry => entry === 'GET /api/albums/page').length;
}

async function openAlbumFromAlbums(dom, harness, scrollTop) {
  await dom.window.navigate('albums');
  const grid = await waitFor(() => dom.window.document.querySelector('#albums-grid .album-card, #albums-grid > *')
    && dom.window.document.querySelector('#albums-grid'));
  // jsdom does not lay out: emulate a scroll offset that, like a browser's,
  // clamps to 0 as soon as the view's content is removed.
  const main = dom.window.document.querySelector('#main');
  const viewContainer = dom.window.document.querySelector('#view-container');
  let mainScrollTop = 0;
  Object.defineProperty(main, 'scrollTop', {
    configurable: true,
    get: () => {
      if (!viewContainer.childElementCount) mainScrollTop = 0;
      return mainScrollTop;
    },
    set: v => { mainScrollTop = viewContainer.childElementCount ? v : 0; },
  });
  main.scrollTop = scrollTop;
  await dom.window.navigate('album', { id: 11 });
  await waitFor(() => dom.window.document.querySelector('.back-btn'));
  return { grid, main };
}

async function testBackToAlbumsRestoresKeptViewWithoutRefetch() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const { grid, main } = await openAlbumFromAlbums(dom, harness, 640);
    const fetchesBefore = albumListFetches(harness);

    dom.window.history.back();
    await waitFor(() => dom.window.eval('state.view') === 'albums' && dom.window.document.querySelector('#albums-grid'));

    assert.equal(albumListFetches(harness), fetchesBefore, 'back must not refetch the album list');
    assert.equal(dom.window.document.querySelector('#albums-grid'), grid, 'back must reuse the same grid node');
    assert.equal(main.scrollTop, 640, 'back must restore the scroll position');
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testMenuNavigationAndMutationsRenderAlbumsFresh() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    await openAlbumFromAlbums(dom, harness, 200);
    let fetches = albumListFetches(harness);
    dom.window.document.querySelector('.nav-item[data-view="albums"]').click();
    await waitFor(() => albumListFetches(harness) === fetches + 1);

    await openAlbumFromAlbums(dom, harness, 200);
    fetches = albumListFetches(harness);
    await dom.window.api('/albums/11/favorite', { method: 'POST' });
    dom.window.history.back();
    await waitFor(() => albumListFetches(harness) === fetches + 1);
    assert.equal(dom.window.eval('state.view'), 'albums');
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testAlbumsReRenderDoesNotStackScrollListeners() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const main = dom.window.document.querySelector('#main');
    let scrollListeners = 0;
    const add = main.addEventListener.bind(main);
    const remove = main.removeEventListener.bind(main);
    main.addEventListener = (type, ...rest) => { if (type === 'scroll') scrollListeners += 1; return add(type, ...rest); };
    main.removeEventListener = (type, ...rest) => { if (type === 'scroll') scrollListeners -= 1; return remove(type, ...rest); };

    for (let i = 0; i < 3; i += 1) {
      await dom.window.navigate('albums');
      await waitFor(() => dom.window.document.querySelector('#albums-grid > *'));
    }
    assert.equal(scrollListeners, 1, 'albums must keep a single scroll listener on #main');
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testGenreViewDoesNotReuseCachedMarkupAcrossIds() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    await dom.window.navigate('genre', { id: 'Rock' });
    await waitFor(() => dom.window.document.querySelector('.section-title')?.textContent === 'Rock');

    await dom.window.navigate('genre', { id: 'Jazz' });
    await waitFor(() => dom.window.document.querySelector('.section-title')?.textContent === 'Jazz');

    assert.equal(dom.window.document.querySelector('.section-title')?.textContent, 'Jazz');
    assert.match(dom.window.document.querySelector('#genre-albums-grid')?.textContent || '', /Jazz Album/);
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testExitSearchReplacesHistoryEntryInsteadOfPushingAnother() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const initialHistoryLength = dom.window.history.length;

    await dom.window.navigate('search', { q: 'alpha' });
    await waitFor(() => dom.window.location.hash === '#search');

    const afterSearchHistoryLength = dom.window.history.length;
    assert.equal(afterSearchHistoryLength, initialHistoryLength + 1);

    dom.window.eval('exitSearch()');
    await waitFor(() => dom.window.eval('state.view') === 'home');

    assert.equal(dom.window.location.hash, '#home');
    assert.equal(dom.window.history.length, afterSearchHistoryLength);
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testNavigateClosesMobileSidebarOverlay() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const overlay = dom.window.document.querySelector('#sidebar-overlay');
    const sidebar = dom.window.document.querySelector('#sidebar');

    overlay.classList.add('open', 'active');
    sidebar.classList.add('open');

    await dom.window.navigate('albums');

    assert.equal(overlay.classList.contains('open'), false);
    assert.equal(overlay.classList.contains('active'), false);
    assert.equal(sidebar.classList.contains('open'), false);
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testTabletMenuNavigationDoesNotFocusSearch() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    // OPPO Pad landscape is wide enough to look desktop-like to width-only checks.
    Object.defineProperty(dom.window, 'innerWidth', { configurable: true, writable: true, value: 1131 });
    Object.defineProperty(dom.window, 'innerHeight', { configurable: true, writable: true, value: 800 });
    const document = dom.window.document;
    const homeSearch = await waitFor(() => document.querySelector('#search-input'));
    assert.notEqual(document.activeElement, homeSearch, 'home search must wait for an explicit tap');

    homeSearch.focus();
    assert.equal(document.activeElement, homeSearch);

    document.querySelector('#mobile-menu-btn').click();
    assert.notEqual(document.activeElement, homeSearch, 'opening the menu should dismiss the tablet keyboard');

    document.querySelector('.nav-item[data-view="albums"]').click();
    await waitFor(() => dom.window.eval('state.view') === 'albums');
    const albumSearch = await waitFor(() => document.querySelector('#search-input[placeholder="Search albums or tracks…"]'));
    assert.notEqual(
      document.activeElement,
      albumSearch,
      'album menu navigation must not focus the new search field',
    );

    albumSearch.focus();
    document.querySelector('#mobile-menu-btn').click();
    document.querySelector('.nav-item[data-view="artists"]').click();
    await waitFor(() => dom.window.eval('state.view') === 'artists');
    const artistSearch = await waitFor(() => document.querySelector('#search-input[placeholder="Search artists…"]'));
    assert.notEqual(document.activeElement, artistSearch, 'artist menu navigation must not focus search');

    artistSearch.focus();
    document.querySelector('#mobile-menu-btn').click();
    document.querySelector('.nav-item[data-view="home"]').click();
    await waitFor(() => dom.window.eval('state.view') === 'home');
    const nextHomeSearch = await waitFor(() => document.querySelector('#search-input[placeholder="Search artists, albums, tracks…"]'));
    assert.notEqual(document.activeElement, nextHomeSearch, 'home menu navigation must not focus search');
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

async function testSearchTrackOpensAlbumAndPlaysMatchedTrack() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    await dom.window.navigate('search', { q: 'alpha' });
    await waitFor(() => dom.window.document.querySelector('.track-item'));

    let playedArgs = null;
    dom.window.playTrack = (...args) => {
      playedArgs = args;
      return Promise.resolve();
    };

    dom.window.document.querySelector('.track-item').click();

    await waitFor(() => dom.window.eval('state.view') === 'album');
    await waitFor(() => playedArgs !== null);

    assert.equal(dom.window.eval('state.viewParams.id'), 11);
    assert.equal(playedArgs[0].id, 101);
    assert.equal(playedArgs[2], 0);
  } finally {
    await new Promise(resolve => dom.window.setTimeout(resolve, 10));
  }
}

function testLandscapeAlbumLayoutCssGuards() {
  const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8'));

  assert.ok(
    html.includes('grid-template-columns: clamp(108px, 20vw, 136px) minmax(0, 1fr) !important;'),
    'Expected landscape album hero to keep cover art and metadata in balanced columns',
  );
  assert.ok(
    html.includes('grid-template-columns: repeat(2, minmax(0, 1fr)) !important;'),
    'Expected landscape album actions to use a balanced two-column grid',
  );
  assert.ok(
    !html.includes('class="album-hero-title" style="display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap"'),
    'Expected album title markup to stop forcing centered inline layout',
  );
}

async function main() {
  await testAlbumBackFallsBackWhenHistoryBackDoesNothing();
  await testBackToAlbumsRestoresKeptViewWithoutRefetch();
  await testMenuNavigationAndMutationsRenderAlbumsFresh();
  await testAlbumsReRenderDoesNotStackScrollListeners();
  await testGenreViewDoesNotReuseCachedMarkupAcrossIds();
  await testExitSearchReplacesHistoryEntryInsteadOfPushingAnother();
  await testNavigateClosesMobileSidebarOverlay();
  await testTabletMenuNavigationDoesNotFocusSearch();
  await testSearchTrackOpensAlbumAndPlaysMatchedTrack();
  testLandscapeAlbumLayoutCssGuards();
}

try {
  await main();
  console.log('frontend_navigation_back=OK');
  process.exit(0);
} catch (error) {
  console.error('frontend_navigation_back=FAILED');
  console.error(error);
  process.exit(1);
}
