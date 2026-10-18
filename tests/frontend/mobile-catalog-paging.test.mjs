import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const albums = Array.from({ length: 137 }, (_, index) => ({
  id: index + 1, title: `Album ${index + 1}`, artist_name: 'Artist',
  genre: 'Classical', year: 2024, is_favorite: false,
}));
const requests = [];
const response = data => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  async json() { return structuredClone(data); },
  async text() { return JSON.stringify(data); },
});
const request = async (url, options = {}) => {
  const u = new URL(url, 'http://localhost');
  requests.push(u);
  const q = u.searchParams.get('q') || '';
  if (u.pathname === '/api/artists/page') {
    const offset = Number(u.searchParams.get('offset') || 0);
    const items = Array.from({ length: 75 }, (_, i) => ({ id: i + 1, name: 'Artist ' + (i + 1), album_count: 2 }));
    return response({ artists: items.slice(offset, offset + 60), total: items.length });
  }
  if (u.pathname === '/api/albums/page') {
    if (u.searchParams.get('search')) {
      return response({ albums: albums.filter(x => x.title.includes('137')),
                        total: 1, limit: 60, offset: 0 });
    }
    const offset = Number(u.searchParams.get('offset') || 0);
    return response({
      albums: albums.slice(offset, offset + Number(u.searchParams.get('limit') || 60)),
      total: albums.length, genres: ['Classical'], decades: ['2020s'],
    });
  }
  if (u.pathname === '/api/search') {
    return response({ tracks: q === '137' ? [{
      id: 777, title: '137 Track', album_id: 137,
      album_title: 'Album 137', artist_name: 'Artist',
    }] : [] });
  }
  if (u.pathname === '/api/stats') return response({ albums: 137, artists: 1, tracks: 137 });
  if (u.pathname === '/api/security/status') return response({ enabled: false });
  if (u.pathname === '/api/dsp/profiles') return response([]);
  if (u.pathname === '/api/scan/status') return response({ running: false });
  return response({});
};
const html = inlineFrontendModules(readFileSync('static/index.html', 'utf8'))
  .replace(/<link[^>]+>/g, '');
const dom = new JSDOM(html, {
  url: 'http://localhost/#albums', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = request;
    window.alert = () => {};
    window.confirm = () => true;
    window.scrollTo = () => {};
    window.HTMLElement.prototype.scrollTo = () => {};
    window.matchMedia = q => ({
      media: q, matches: false, addListener() {}, removeListener() {},
      addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
    });
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });
    window.requestAnimationFrame = cb => window.setTimeout(cb, 0);
    window.cancelAnimationFrame = id => window.clearTimeout(id);
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
    window.HTMLMediaElement.prototype.load = () => {};
    window.MediaMetadata = class {};
    window.navigator.mediaSession = { setActionHandler() {}, metadata: null };
  },
});
const wait = async predicate => {
  for (let i=0; i<200; i++) {
    if (predicate()) return;
    await new Promise(resolve=>setTimeout(resolve,20));
  }
  throw new Error('Timed out waiting for mobile catalog');
};
await wait(()=>dom.window.document.querySelectorAll('#albums-grid .card').length===60);
assert.equal(dom.window.document.querySelectorAll('#albums-grid .card').length, 60,
             'Only first album page is attached');
assert.ok(!requests.some(u=>u.pathname==='/api/albums' && Number(u.searchParams.get('limit'))>200));
const next=dom.window.document.querySelector('#next-page-btn');
next.click();
await wait(()=>requests.some(u=>u.pathname==='/api/albums/page' && u.searchParams.get('offset')==='60'));
await wait(()=>dom.window.document.querySelector('#albums-grid .card .card-title')?.textContent==='Album 61');
const input=dom.window.document.querySelector('#search-input');
input.value='137';
input.dispatchEvent(new dom.window.Event('input',{ bubbles:true }));
await wait(()=>requests.some(u=>u.pathname==='/api/search' && u.searchParams.get('q')==='137'));
await wait(()=>dom.window.document.querySelector('#albums-grid')?.textContent.includes('137 Track'));
assert.equal(requests.some(u=>u.pathname==='/api/search' && !u.searchParams.get('q')), false,
             'Never ask server to search with an empty query');
await dom.window.navigate('artists');
await wait(() => dom.window.document.querySelectorAll('#artists-grid .card').length === 60);
assert.equal(requests.filter(u => u.pathname === '/api/artists/page').at(-1).searchParams.get('sort'), 'popular');
dom.window.document.querySelector('#next-page-btn').click();
await wait(() => dom.window.document.querySelectorAll('#artists-grid .card').length === 15);
assert.equal(requests.filter(u => u.pathname === '/api/artists/page').at(-1).searchParams.get('sort'), 'popular');
const artistSort = dom.window.document.querySelector('#artist-sort-select');
artistSort.value = 'name';
artistSort.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
await wait(() => dom.window.document.querySelectorAll('#artists-grid .card').length === 60);
const lastArtists = requests.filter(u => u.pathname === '/api/artists/page').at(-1);
assert.equal(lastArtists.searchParams.get('sort'), 'name');
assert.equal(lastArtists.searchParams.get('offset'), '0');
console.log('frontend_mobile_catalog_paging_check=OK');
dom.window.close();
