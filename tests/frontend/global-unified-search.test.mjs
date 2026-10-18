import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

// Unified global search must render Artists, then Albums, then Tracks from
// any topbar. The search page does NOT download complete catalogs to clients.
const artist = { id: 3, name: '徐佳瑩', sort_name: '徐佳瑩', album_count: 1,
                 aliases: ['LaLa Hsu'] };
const album = { id: 7, title: '理想人生', artist_id: 3, artist_name: '徐佳瑩',
                track_count: 1, year: 2012 };
const track = { id: 30, title: '失落沙洲', album_id: 7,
                album_title: '理想人生', artist_name: '徐佳瑩', format: 'FLAC' };
const requests = [];
// Becomes true once the user adds "Lala Alias" with + Alias.
let manualAliasAdded = false;
const response = data => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  async json() { return structuredClone(data); },
  async text() { return JSON.stringify(data); },
});
const request = async (url, opts = {}) => {
  const u = new URL(url, 'http://localhost');
  requests.push({ url: u, method: opts.method || 'GET' });
  const q = u.searchParams.get('q') || '';
  if (u.pathname === '/api/artists/3/aliases' && opts.method === 'POST') {
    manualAliasAdded = JSON.parse(opts.body).name === 'Lala Alias';
    return response({ status: 'ok', added: true, aliases: ['LaLa Hsu', 'Lala Alias'] });
  }
  if (u.pathname === '/api/search/category') {
    const kind = u.searchParams.get('kind');
    const continuation = {
      artists: [{ ...artist, id: 4, name: 'Another Artist' }],
      albums: [{ ...album, id: 8, title: 'Another Album' }],
      tracks: [{ ...track, id: 31, title: 'Another Song' }],
    };
    return response({ kind, items: continuation[kind] || [], total: 2,
                      offset: Number(u.searchParams.get('offset')), limit: 40 });
  }
  if (u.pathname === '/api/search') {
    if (q === 'paging') {
      return response({ artists: [artist], albums: [album], tracks: [track],
                        total_artists: 2, total_albums: 2, total_tracks: 2 });
    }
    if (/lala hsu|徐佳莹|徐佳瑩/i.test(q) || (q === 'Lala Alias' && manualAliasAdded)) {
      return response({ artists: [artist], albums: [album], tracks: [track],
                        total_artists: 1, total_albums: 1, total_tracks: 1 });
    }
    return response({ artists: [], albums: [], tracks: [],
                      total_artists: 0, total_albums: 0, total_tracks: 0 });
  }
  if (u.pathname === '/api/stats') return response({ albums: 1, artists: 1, tracks: 1 });
  if (u.pathname === '/api/albums/page') return response({
    albums: [album], total: 1, genres: [], decades: [],
  });
  if (u.pathname === '/api/artists/page') return response({ artists: [artist], total: 1 });
  if (u.pathname === '/api/artists/3') return response(artist);
  if (u.pathname === '/api/artists/3/albums') return response([album]);
  if (u.pathname === '/api/artists/3/enrich-bio') return response({});
  if (u.pathname === '/api/recently-added') return response([]);
  if (u.pathname === '/api/security/status') return response({ enabled: false });
  if (u.pathname === '/api/dsp/profiles') return response([]);
  if (u.pathname === '/api/scan/status') return response({ running: false });
  return response({});
};
const html = inlineFrontendModules(readFileSync('static/index.html', 'utf8'))
  .replace(/<link[^>]+>/g, '');
const dom = new JSDOM(html, {
  url: 'http://localhost/#home', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = request;
    window.alert = () => {};
    window.confirm = () => true;
    window.prompt = () => 'Lala Alias';
    window.scrollTo = () => {};
    window.matchMedia = q => ({
      media:q, matches:false, addListener(){}, removeListener(){},
      addEventListener(){}, removeEventListener(){},
      dispatchEvent(){ return false; },
    });
    window.requestAnimationFrame = cb => window.setTimeout(cb,0);
    window.cancelAnimationFrame = id => window.clearTimeout(id);
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
    window.HTMLMediaElement.prototype.load = () => {};
    window.MediaMetadata = class {};
    window.navigator.mediaSession = { setActionHandler(){}, metadata:null };
  },
});
const wait = async predicate => {
  for (let i = 0; i < 170; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error('Timed out waiting for global search results');
};
const searchFromCurrentView = async text => {
  await wait(() => !!dom.window.document.querySelector('#search-input'));
  const input = dom.window.document.querySelector('#search-input');
  input.value = text;
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await wait(() => dom.window.document.querySelector('#global-search-results')
    ?.textContent.includes('失落沙洲'));
};
await wait(() => dom.window.document.querySelector('#search-input'));

// From HOME: Latin nickname -> Chinese artist and its album/tracks.
await searchFromCurrentView('Lala Hsu');
const titles = [...dom.window.document.querySelectorAll(
  '#global-search-results .result-section-title'
)].map(el => el.textContent.split(' — ')[0]);
assert.deepEqual(titles, ['Artists', 'Albums', 'Tracks']);
assert.equal(dom.window.document.querySelectorAll('.search-artist-grid .artist-card').length, 1);
assert.equal(dom.window.document.querySelectorAll('.search-albums-grid .card').length, 1);
assert.equal(dom.window.document.querySelectorAll('.search-track-list .track-item').length, 1);

// From ALBUMS: the same topbar does not restrict search to album-only matches.
await dom.window.navigate('albums');
await wait(() => dom.window.document.querySelector('#albums-grid'));
await searchFromCurrentView('徐佳莹');
assert.deepEqual([...dom.window.document.querySelectorAll(
  '#global-search-results .result-section-title'
)].map(el => el.textContent.split(' — ')[0]), ['Artists', 'Albums', 'Tracks']);

// From ARTISTS: global search can still show albums and songs.
await dom.window.navigate('artists');
await wait(() => dom.window.document.querySelector('#artists-grid'));
assert.equal(dom.window.document.querySelector('#artist-enrichment-panel'), null,
             'MusicBrainz enrichment is not part of this release');
await searchFromCurrentView('Lala Hsu');

// Artist cards retain navigation; only the manual alias control remains.
const artistCard = dom.window.document.querySelector('.search-artist-grid .artist-card');
artistCard.click();
await wait(() => !!dom.window.document.querySelector('#artist-add-alias-btn'));
assert.equal(dom.window.document.querySelector('#artist-sync-alias-btn'), null);

assert.equal(requests.some(item => item.url.pathname === '/api/search'
                       && !item.url.searchParams.get('q')), false);

// An unknown stage name never triggers a remote lookup; the empty state points
// to + Alias. After the user adds the alias, the same search finds the artist.
await dom.window.navigate('home');
await wait(() => dom.window.document.querySelector('#search-input'));
const missingInput = dom.window.document.querySelector('#search-input');
missingInput.value = 'Lala Alias';
missingInput.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
await wait(() => dom.window.document.querySelector('#global-search-results')
  ?.textContent.includes('+ Alias'));
assert.equal(dom.window.document.querySelector('#search-resolve-alias-btn'), null);
assert.equal(requests.some(item => item.url.pathname.includes('musicbrainz')
  || item.url.pathname === '/api/search/resolve-artist-alias'), false);
await dom.window.navigate('artist', { id: 3 });
await wait(() => !!dom.window.document.querySelector('#artist-add-alias-btn'));
dom.window.document.querySelector('#artist-add-alias-btn').click();
await wait(() => manualAliasAdded);
await wait(() => dom.window.document.querySelector('#artist-aliases-list')
  ?.textContent.includes('Lala Alias'));
await dom.window.navigate('home');
await searchFromCurrentView('Lala Alias');

// Every category supports continuation without navigating to a special menu.
await dom.window.navigate('home');
await wait(() => dom.window.document.querySelector('#search-input'));
await searchFromCurrentView('paging');
for (const [kind, selector] of [
  ['artists', '.search-artist-grid .artist-card'],
  ['albums', '.search-albums-grid .card'],
  ['tracks', '.search-track-list .track-item'],
]) {
  const button = dom.window.document.querySelector(
    `[data-search-category="${kind}"]`
  );
  assert.ok(button, `Missing Load more button for ${kind}`);
  button.click();
  await wait(() => dom.window.document.querySelectorAll(selector).length === 2);
}
assert.equal(requests.filter(item => item.url.pathname === '/api/search/category').length, 3);

// ── Request ordering on the unified search view ──────────────────────────────
// The unified search view is now the ONLY place a query is issued, so its
// render serial guard is the single thing standing between a slow response and
// a stale result set. These cases keep the invariants main's per-page Tracks
// search used to guard, now applied to /api/search.
const raceWait = async predicate => {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Timed out waiting for search request ordering');
};

async function createRaceHarness() {
  const pending = [];
  const raceDom = new JSDOM(html, {
    url: 'http://localhost/#home', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = async url => {
        const parsed = new URL(url, 'http://localhost');
        if (parsed.pathname === '/api/search') {
          return new Promise(resolve => pending.push({
            query: parsed.searchParams.get('q'),
            resolve(data) { resolve(response(data)); },
          }));
        }
        if (['/api/dsp/profiles', '/api/recently-added', '/api/stats'].includes(parsed.pathname)) {
          return response([]);
        }
        return response({});
      };
      window.alert = () => {};
      window.scrollTo = () => {};
      window.matchMedia = q => ({
        media: q, matches: false, addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
      });
      window.HTMLMediaElement.prototype.play = () => Promise.resolve();
      window.HTMLMediaElement.prototype.pause = () => {};
      window.HTMLMediaElement.prototype.load = () => {};
      window.MediaMetadata = class {};
      window.navigator.mediaSession = { setActionHandler() {}, metadata: null };
    },
  });
  await raceWait(() => raceDom.window.document.querySelector('.home-hero'));
  const type = value => {
    const input = raceDom.window.document.querySelector('#search-input');
    input.value = value;
    input.dispatchEvent(new raceDom.window.Event('input', { bubbles: true }));
  };
  return { dom: raceDom, pending, type };
}

const racePayload = title => ({
  artists: [{ id: 1, name: title, sort_name: title, album_count: 1, aliases: [] }],
  albums: [], tracks: [], total_artists: 1, total_albums: 0,
});

// A slow older query must never overwrite a newer one.
{
  const h = await createRaceHarness();
  try {
    h.type('slow');
    await raceWait(() => h.pending.length === 1);
    h.type('latest');
    await raceWait(() => h.pending.length === 2);
    assert.equal(h.pending[1].query, 'latest');
    h.pending[1].resolve(racePayload('LatestArtist'));
    await raceWait(() => h.dom.window.document.querySelector('#global-search-groups')
      ?.textContent.includes('LatestArtist'));
    h.pending[0].resolve(racePayload('StaleArtist'));
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.ok(!h.dom.window.document.querySelector('#global-search-groups')
      .textContent.includes('StaleArtist'), 'A stale search response must not replace newer results');
  } finally { h.dom.window.close(); }
}

// Leaving the search view discards its in-flight response entirely.
{
  const h = await createRaceHarness();
  try {
    h.type('first');
    await raceWait(() => h.pending.length === 1);
    h.pending[0].resolve(racePayload('FirstArtist'));
    await raceWait(() => h.dom.window.document.querySelector('#global-search-groups')
      ?.textContent.includes('FirstArtist'));
    const groups = h.dom.window.document.querySelector('#global-search-groups');
    h.type('pending');
    await raceWait(() => h.pending.length === 2);
    await h.dom.window.navigate('home');
    h.pending[1].resolve(racePayload('DetachedArtist'));
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.ok(groups.textContent.includes('FirstArtist')
      && !groups.textContent.includes('DetachedArtist'),
      'Discard search responses for a detached view instance');
  } finally { h.dom.window.close(); }
}

// Clearing the query must not fetch the whole catalog.
{
  const h = await createRaceHarness();
  try {
    h.type('something');
    await raceWait(() => h.pending.length === 1);
    h.type('');
    await new Promise(resolve => setTimeout(resolve, 450));
    assert.equal(h.pending.length, 1, 'An empty query must not issue a catalog request');
    assert.ok(!h.dom.window.document.querySelector('#global-search-results'),
      'Clearing the query exits the search view');
  } finally { h.dom.window.close(); }
}

console.log('frontend_global_unified_search=OK');
dom.window.close();
