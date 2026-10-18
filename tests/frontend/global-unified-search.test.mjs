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
let resolvedUnknownAlias = false;
let resolvedLateAlias = false;
const response = data => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  async json() { return structuredClone(data); },
  async text() { return JSON.stringify(data); },
});
const request = async (url, opts = {}) => {
  const u = new URL(url, 'http://localhost');
  requests.push({ url: u, method: opts.method || 'GET' });
  const q = u.searchParams.get('q') || '';
  if (u.pathname === '/api/search/resolve-artist-alias') {
    if (q === 'Late Alias' && u.searchParams.get('detail_offset') !== '5') {
      return response({ status: 'more_candidates', next_offset: 5, remaining: 1 });
    }
    return response({ status: 'needs_review', artist_id: 3,
      candidate: { mbid: 'a47ac8f5-6774-4540-842f-f1eb0a19845c',
                   name: '徐佳瑩', country: 'TW' } });
  }
  if (u.pathname === '/api/artists/3/aliases/confirm') {
    if (q === 'Late Alias' || JSON.parse(opts.body).query_alias === 'Late Alias') {
      resolvedLateAlias = true;
    } else {
      resolvedUnknownAlias = true;
    }
    return response({ status: 'ok', artist_id: 3,
                      aliases: ['LaLa Hsu', 'Lala Alias'], added: ['Lala Alias'] });
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
    if (/lala hsu|徐佳莹|徐佳瑩/i.test(q) || (q === 'Lala Alias' && resolvedUnknownAlias) || (q === 'Late Alias' && resolvedLateAlias)) {
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
    window.prompt = () => '1';
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
// MusicBrainz enrichment is withdrawn. The Artists view must neither render the
// panel nor poll a background enrichment job it no longer offers.
assert.equal(dom.window.document.querySelector('#artist-enrichment-panel'), null,
             'MusicBrainz enrichment is not part of this release');
assert.equal(requests.some(item => item.url.pathname.includes('/artist-aliases/enrichment')),
             false, 'The Artists view must not poll an enrichment job');
await searchFromCurrentView('Lala Hsu');

// Artist cards retain navigation, and the manual alias control remains.
const artistCard = dom.window.document.querySelector('.search-artist-grid .artist-card');
artistCard.click();
await wait(() => !!dom.window.document.querySelector('#artist-add-alias-btn'));
assert.equal(dom.window.document.querySelector('#artist-sync-alias-btn'), null,
             'Sync Names has been withdrawn from the artist page');
await wait(() => dom.window.document.querySelector('#artist-aliases-list')?.textContent.includes('LaLa Hsu'));

assert.equal(requests.some(item => item.url.pathname === '/api/search'
                       && !item.url.searchParams.get('q')), false);

// No automatic remote network lookup on keystrokes. The user explicitly opts in
// when searching a stage name not yet known to the local library.
resolvedUnknownAlias = false;
await dom.window.navigate('home');
await wait(() => dom.window.document.querySelector('#search-input'));
const missingInput = dom.window.document.querySelector('#search-input');
missingInput.value = 'Lala Alias';
missingInput.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
await wait(() => dom.window.document.querySelector('#search-resolve-alias-btn'));
assert.equal(requests.some(item => item.url.pathname === '/api/search/resolve-artist-alias'), false);
const findAlias = dom.window.document.querySelector('#search-resolve-alias-btn');
findAlias.dispatchEvent(new dom.window.MouseEvent('pointerdown', { bubbles: true }));
assert.equal(findAlias.isConnected, true,
             'Pointerdown on the alias resolver must not close the search view');
findAlias.click();
await wait(() => resolvedUnknownAlias);
await wait(() => dom.window.document.querySelector('.search-artist-grid .artist-card'));
assert.equal(dom.window.document.querySelectorAll('.search-albums-grid .card').length, 1);
assert.equal(requests.some(item => item.url.pathname === '/api/artists/3/aliases/confirm'), true,
             'An unverified identity requires a second explicit approval request');

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
        if (parsed.pathname === '/api/search/resolve-artist-alias') return response({ status: 'not_found' });
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

// Every global search entry keeps spaces, the focused editor, and its caret.
for (const origin of ['home', 'albums', 'artists', 'discover', 'tracks']) {
  const h = await createRaceHarness();
  try {
    if (origin !== 'home') await h.dom.window.navigate(origin);
    const document = h.dom.window.document;
    await raceWait(() => document.querySelector('#search-input'));
    const originalInput = document.querySelector('#search-input');
    originalInput.focus();
    h.type('Lala ');
    await raceWait(() => h.pending.length === 1);
    assert.equal(h.pending[0].query, 'Lala', 'Only the API query should be trimmed');
    // A separator typed during a slow response must survive the first render.
    h.type('Lala  ');
    h.pending[0].resolve(racePayload('FirstArtist'));
    await raceWait(() => document.querySelector('#global-search-groups')
      ?.textContent.includes('FirstArtist'));
    const input = document.querySelector('#search-input');
    assert.equal(input, originalInput, origin + ' must keep the keyboard editor connected');
    assert.equal(input.value, 'Lala  ', origin + ' must preserve trailing spaces');
    assert.equal(document.activeElement, input);
    const historyLength = h.dom.window.history.length;
    h.type('Lala   ');
    await new Promise(resolve => setTimeout(resolve, 400));
    assert.equal(h.pending.length, 1, 'A separator must not refetch the same query');
    assert.equal(document.querySelector('#search-input'), input);
    assert.equal(document.activeElement, input);
    assert.equal(h.dom.window.history.length, historyLength);

    h.type('Lala Hsu');
    await raceWait(() => h.pending.length === 2);
    input.setSelectionRange(4, 4);
    h.pending[1].resolve(racePayload('SecondArtist'));
    await raceWait(() => document.querySelector('#global-search-groups')
      ?.textContent.includes('SecondArtist'));
    assert.equal(h.pending[1].query, 'Lala Hsu');
    assert.equal(document.querySelector('#search-input'), input,
      'Result updates must keep the live input node');
    assert.equal(document.activeElement, input);
    assert.equal(input.selectionStart, 4, 'Result updates must not move the caret');

    h.type(' ');
    await new Promise(resolve => setTimeout(resolve, 400));
    assert.equal(h.dom.window.eval('state.view'), 'search', 'Space alone must not exit search');
    assert.equal(document.activeElement, input);
    assert.equal(h.pending.length, 2, 'Whitespace must not request the whole catalog');
    h.type('');
    await raceWait(() => h.dom.window.eval('state.view') === origin
      && document.querySelector('#view-container').style.opacity === '1'
      && document.querySelector('#search-input'));
  } finally { h.dom.window.close(); }
}

// Space/autocorrect commits may temporarily empty the input before replacing
// Kelly with "Kelly ". None of these intermediate events should exit search.
for (const sequence of ['composing-flag-only', 'compositionend-before-input', 'replacement']) {
  const h = await createRaceHarness();
  try {
    h.type('Kelly');
    await raceWait(() => h.pending.length === 1);
    h.pending[0].resolve(racePayload('Kelly'));
    const document = h.dom.window.document;
    await raceWait(() => document.querySelector('#global-search-groups')?.textContent.includes('Kelly'));
    const input = document.querySelector('#search-input');
    input.focus();
    let exits = 0;
    const exit = h.dom.window.exitSearch;
    h.dom.window.exitSearch = () => { exits++; return exit(); };
    if (sequence === 'compositionend-before-input') {
      input.dispatchEvent(new h.dom.window.CompositionEvent('compositionstart', { bubbles: true }));
    }
    input.value = '';
    input.dispatchEvent(new h.dom.window.InputEvent('input', {
      bubbles: true, inputType: sequence === 'replacement' ? 'deleteContentBackward' : 'insertCompositionText',
      isComposing: sequence !== 'replacement',
    }));
    if (sequence === 'compositionend-before-input') {
      input.dispatchEvent(new h.dom.window.CompositionEvent('compositionend', { bubbles: true }));
    }
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(exits, 0, sequence + ': transient emptiness must not dismiss the editor');
    input.value = 'Kelly ';
    input.dispatchEvent(new h.dom.window.InputEvent('input', {
      bubbles: true, inputType: 'insertReplacementText', data: 'Kelly ',
    }));
    await new Promise(resolve => setTimeout(resolve, 400));
    assert.equal(exits, 0);
    assert.equal(h.dom.window.eval('state.view'), 'search');
    assert.equal(document.querySelector('#search-input'), input);
    assert.equal(input.value, 'Kelly ');
    assert.equal(document.activeElement, input);
    assert.equal(h.pending.length, 1, 'A replacement with the same query must not refetch');
    h.type('');
    await raceWait(() => exits === 1);
    assert.equal(h.dom.window.eval('state.view'), 'home', 'A settled clear still exits search');
  } finally { h.dom.window.close(); }
}

// Starting composition cancels a query queued before the IME took over.
{
  const h = await createRaceHarness();
  try {
    const input = h.dom.window.document.querySelector('#search-input');
    input.focus();
    h.type('Kel');
    input.dispatchEvent(new h.dom.window.CompositionEvent('compositionstart', { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 400));
    assert.equal(h.pending.length, 0, 'No request should navigate away during composition');
    input.value = 'Kelly ';
    input.dispatchEvent(new h.dom.window.CompositionEvent('compositionend', { bubbles: true }));
    await raceWait(() => h.pending.length === 1);
    assert.equal(h.pending[0].query, 'Kelly');
  } finally { h.dom.window.close(); }
}

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

// A matching English alias at candidate #6 is available on a second,
// explicitly requested MusicBrainz lookup rather than silently omitted.
await dom.window.navigate('home');
await wait(() => dom.window.document.querySelector('#search-input'));
const late = dom.window.document.querySelector('#search-input');
late.value = 'Late Alias';
late.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
await wait(() => dom.window.document.querySelector('#search-resolve-alias-btn'));
dom.window.document.querySelector('#search-resolve-alias-btn').click();
await wait(() => requests.some(item => item.url.pathname === '/api/search/resolve-artist-alias'
  && item.url.searchParams.get('q') === 'Late Alias'
  && item.url.searchParams.get('detail_offset') === '0'));
// "Checking MusicBrainz…" also contains "Check": wait until the first lookup
// has finished and re-enabled the button, or the second click is dropped.
await wait(() => {
  const button = dom.window.document.querySelector('#search-resolve-alias-btn');
  return button && !button.disabled && button.textContent.includes('more MusicBrainz candidates');
});
dom.window.document.querySelector('#search-resolve-alias-btn').click();
await wait(() => requests.some(item => item.url.pathname === '/api/search/resolve-artist-alias'
  && item.url.searchParams.get('detail_offset') === '5'));
await wait(() => resolvedLateAlias);
await wait(() => dom.window.document.querySelector('.search-artist-grid .artist-card'));

console.log('frontend_global_unified_search=OK');
dom.window.close();
