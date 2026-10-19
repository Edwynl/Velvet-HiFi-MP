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
    const region = u.searchParams.get('region') || '';
    const search = u.searchParams.get('search') || '';
    const all = Array.from({ length: 75 }, (_, i) => ({ id: i + 1, name: 'Artist ' + (i + 1), album_count: 2, region: i < 65 ? 'korea' : 'unknown' }))
      .filter(item => item.name.includes(search));
    const items = all.filter(item => !region || item.region === region);
    return response({ artists: items.slice(offset, offset + 60), total: items.length, all_total: all.length,
      regions: [{ region: 'korea', label: 'Korea', count: all.filter(a=>a.region==='korea').length },
                { region: 'unknown', label: 'Other', count: all.filter(a=>a.region==='unknown').length }] });
  }
  if (u.pathname === '/api/albums/page') {
    if (u.searchParams.get('search')) {
      return response({ albums: albums.filter(x => x.title.includes('137')),
                        total: 1, limit: 60, offset: 0 });
    }
    const offset = Number(u.searchParams.get('offset') || 0);
    return response({
      albums: albums.slice(offset, offset + Number(u.searchParams.get('limit') || 60)),
      total: albums.length, genres: ['Classical', 'Asian Pop'], decades: ['2020s'],
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
// A selected sort/filter must be visible and match the request, including
// after leaving page 2 and when clearing genre/decade without clearing sort.
const albumDoc = dom.window.document;
const lastAlbumRequest = () => requests.filter(u => u.pathname === '/api/albums/page').at(-1);
// The Albums view opens on a focused shelf; the selects must agree with the
// request that produced the grid on screen.
assert.equal(albumDoc.querySelector('#album-sort-select').selectedOptions[0].textContent, 'Year');
assert.equal(albumDoc.querySelector('#album-genre-select').selectedOptions[0].textContent, 'Asian Pop');
assert.equal(albumDoc.querySelector('#album-decade-select').selectedOptions[0].textContent, '2020s');
assert.equal(lastAlbumRequest().searchParams.get('sort'), 'year');
assert.equal(lastAlbumRequest().searchParams.get('genre'), 'Asian Pop');
assert.equal(lastAlbumRequest().searchParams.get('decade'), '2020s');
assert.equal(albumDoc.querySelector('#clear-focus-btn').hidden, false,
  'a default genre/decade focus offers Clear filters on entry');
async function chooseAlbumOption(id, value, param, label) {
  const select = albumDoc.getElementById(id);
  const before = requests.length;
  select.value = value;
  select.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  await wait(() => requests.length > before && lastAlbumRequest().searchParams.get(param) === value);
  await wait(() => albumDoc.querySelector('#page-info').textContent === 'Page 1 / 3' && !next.disabled);
  assert.equal(select.selectedOptions[0].textContent, label);
  assert.equal(lastAlbumRequest().searchParams.get('offset'), '0');
}
for (const [value, label] of [['year', 'Year'], ['artist', 'Artist'], ['added', 'Added'], ['title', 'A–Z']]) {
  await chooseAlbumOption('album-sort-select', value, 'sort', label);
}
await chooseAlbumOption('album-sort-select', 'artist', 'sort', 'Artist');
// Drop the default genre/decade focus first, then prove that a sort on its own
// still does not offer Clear filters.
albumDoc.querySelector('#clear-focus-btn').click();
await wait(() => albumDoc.querySelector('#clear-focus-btn').hidden);
assert.equal(lastAlbumRequest().searchParams.get('sort'), 'artist');
assert.equal(albumDoc.querySelector('#clear-focus-btn').hidden, true);
await chooseAlbumOption('album-genre-select', 'Classical', 'genre', 'Classical');
await chooseAlbumOption('album-decade-select', '2020s', 'decade', '2020s');
assert.equal(albumDoc.querySelector('#clear-focus-btn').hidden, false);
albumDoc.querySelector('#favorites-filter-btn').click();
await wait(() => lastAlbumRequest().searchParams.get('favorites_only') === 'true' && !next.disabled);
assert.equal(albumDoc.querySelector('#favorites-filter-btn').getAttribute('aria-pressed'), 'true');
albumDoc.querySelector('#clear-focus-btn').click();
await wait(() => albumDoc.querySelector('#clear-focus-btn').hidden);
assert.equal(albumDoc.querySelector('#album-genre-select').selectedOptions[0].textContent, 'All genres');
assert.equal(albumDoc.querySelector('#album-decade-select').selectedOptions[0].textContent, 'All decades');
assert.equal(lastAlbumRequest().searchParams.get('genre'), '');
assert.equal(lastAlbumRequest().searchParams.get('decade'), '');
assert.equal(lastAlbumRequest().searchParams.get('sort'), 'artist');
assert.equal(lastAlbumRequest().searchParams.get('favorites_only'), 'true');
const input=dom.window.document.querySelector('#search-input');
input.value='137';
input.dispatchEvent(new dom.window.Event('input',{ bubbles:true }));
await wait(()=>requests.some(u=>u.pathname==='/api/search' && u.searchParams.get('q')==='137'));
await wait(()=>dom.window.document.querySelector('#global-search-results')?.textContent.includes('137 Track'));
assert.equal(dom.window.document.querySelector('.search-results .result-section-title')?.textContent.startsWith('Tracks'), true,
             'Global search shows the track section when no artist/album matches');
assert.equal(requests.some(u=>u.pathname==='/api/search' && !u.searchParams.get('q')), false,
             'Never ask server to search with an empty query');
await dom.window.navigate('artists');
await wait(() => dom.window.document.querySelectorAll('#artists-grid .card').length === 60);
assert.equal(requests.filter(u => u.pathname === '/api/artists/page').at(-1).searchParams.get('sort'), 'popular');
dom.window.document.querySelector('#next-page-btn').click();
await wait(() => dom.window.document.querySelectorAll('#artists-grid .card').length === 15);
assert.equal(requests.filter(u => u.pathname === '/api/artists/page').at(-1).searchParams.get('sort'), 'popular');
assert.equal(dom.window.document.querySelector('#artist-sort-select'), null, 'Hide the redundant Popular selector');
const document = dom.window.document;
assert.equal(document.querySelector('[data-region=""]').getAttribute('aria-pressed'), 'true');
document.querySelector('[data-region="korea"]').click();
await wait(() => document.querySelectorAll('#artists-grid .card').length === 60 && document.querySelector('#page-info').textContent === 'Page 1 / 2');
assert.equal(requests.filter(u=>u.pathname==='/api/artists/page').at(-1).searchParams.get('region'), 'korea');
document.querySelector('#next-page-btn').click();
await wait(() => document.querySelectorAll('#artists-grid .card').length === 5);
const artistSearch=document.querySelector('#search-input');
artistSearch.value='Artist 75';
artistSearch.dispatchEvent(new dom.window.Event('input', { bubbles:true }));
// The Artists topbar is the unified global search, so typing leaves this page
// instead of filtering it locally, and the region filter must not swallow the
// keystroke. Local artist filtering stays reachable through the global
// search's Artists category, which uses the same search parameter.
await wait(()=>requests.some(u=>u.pathname==='/api/search' && u.searchParams.get('q')==='Artist 75'));
await wait(()=>document.querySelector('#global-search-groups'));
assert.equal(document.querySelector('#artists-grid'), null,
             'The Artists topbar routes to global search rather than filtering locally');
assert.equal(requests.filter(u=>u.pathname==='/api/artists/page').at(-1).searchParams.get('search'), null,
             'Global search must not send a local Artists filter');
// Region filtering is unaffected by that round trip.
await dom.window.navigate('artists');
await wait(()=>document.querySelector('#artists-grid'));
document.querySelector('[data-region="korea"]').click();
await wait(()=>document.querySelectorAll('#artists-grid .card').length === 60);
assert.equal(document.querySelector('#page-info').textContent, 'Page 1 / 2');
document.querySelector('[data-region=""]').click();
await wait(()=>requests.filter(u=>u.pathname==='/api/artists/page').at(-1).searchParams.get('region')==='');
await wait(()=>document.querySelectorAll('#artists-grid .card').length === 60);
assert.equal(document.querySelector('#page-info').textContent, 'Page 1 / 2');
assert.ok(document.querySelector('#artists-grid').textContent.includes('Artist 1'));
// Let the in-flight page render finish before the window closes, otherwise its
// response lands after teardown and throws on the detached document.
await new Promise(r=>setTimeout(r,80));
console.log('frontend_mobile_catalog_paging_check=OK');
dom.window.close();
