import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const response = data => ({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  async json() { return structuredClone(data); },
  async text() { return JSON.stringify(data); },
});
const waitFor = async predicate => {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Timed out waiting for Tracks request/render');
};
const payload = (title, total = 1) => ({
  tracks: [{ id: title, title, artist_name: 'Test artist', duration: 120 }], total,
});

async function createHarness() {
  const pending = [];
  const html = inlineFrontendModules(readFileSync('static/index.html', 'utf8'))
    .replace(/<link[^>]+>/g, '');
  const dom = new JSDOM(html, {
    url: 'http://localhost/#home', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = async url => {
        const parsed = new URL(url, 'http://localhost');
        if (parsed.pathname === '/api/tracks') {
          return new Promise(resolve => pending.push({
            params: parsed.searchParams,
            resolve(data) { resolve(response(data)); },
          }));
        }
        if (['/api/dsp/profiles', '/api/recently-added'].includes(parsed.pathname)) return response([]);
        return response({});
      };
      window.alert = () => {};
      window.scrollTo = () => {};
      window.matchMedia = query => ({
        media: query, matches: false, addListener() {}, removeListener() {},
        addEventListener() {}, removeEventListener() {},
      });
      window.HTMLMediaElement.prototype.play = () => Promise.resolve();
      window.HTMLMediaElement.prototype.pause = () => {};
      window.HTMLMediaElement.prototype.load = () => {};
      window.MediaMetadata = class {};
      window.navigator.mediaSession = { setActionHandler() {} };
    },
  });
  const { window } = dom;
  const doc = window.document;
  await waitFor(() => doc.querySelector('.home-hero'));
  const navigation = window.navigate('tracks');
  await waitFor(() => pending.length === 1);
  pending[0].resolve(payload('Initial', 600));
  await navigation;
  const search = value => {
    const input = doc.getElementById('search-input');
    input.value = value;
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  };
  return { dom, window, doc, pending, search };
}

async function testLatestSearchWins() {
  const h = await createHarness();
  try {
    h.search('slow');
    await waitFor(() => h.pending.length === 2);
    h.search('latest');
    await waitFor(() => h.pending.length === 3);
    assert.equal(h.pending[2].params.get('search'), 'latest');
    h.pending[2].resolve(payload('Latest result'));
    await waitFor(() => h.doc.getElementById('tracks-list').textContent.includes('Latest result'));
    h.pending[1].resolve(payload('Stale result', 1000));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(!h.doc.getElementById('tracks-list').textContent.includes('Stale result'));
    assert.equal(h.doc.getElementById('tracks-count').textContent, '1 track matches');
    assert.equal(h.doc.getElementById('tracks-page-info').textContent, 'Page 1 / 1');
    assert.equal(h.doc.getElementById('tracks-prev-btn').disabled, true);
    assert.equal(h.doc.getElementById('tracks-next-btn').disabled, true);
  } finally { h.dom.window.close(); }
}

async function testTypingInvalidatesBeforeDebounce() {
  const h = await createHarness();
  try {
    h.search('old');
    await waitFor(() => h.pending.length === 2);
    h.search('new');
    h.pending[1].resolve(payload('Old response during debounce'));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(!h.doc.getElementById('tracks-list').textContent.includes('Old response during debounce'),
      'Invalidate as soon as the input changes, before the next request starts');
    await waitFor(() => h.pending.length === 3);
    h.pending[2].resolve(payload('New response'));
    await waitFor(() => h.doc.getElementById('tracks-list').textContent.includes('New response'));
  } finally { h.dom.window.close(); }
}

async function testSortAndPageShareRequestOrdering() {
  const h = await createHarness();
  try {
    h.doc.getElementById('tracks-next-btn').click();
    await waitFor(() => h.pending.length === 2);
    assert.equal(h.pending[1].params.get('offset'), '200');
    const sort = h.doc.getElementById('track-sort');
    sort.value = 'artist';
    sort.dispatchEvent(new h.window.Event('change', { bubbles: true }));
    await waitFor(() => h.pending.length === 3);
    assert.equal(h.pending[2].params.get('offset'), '0');
    assert.equal(h.pending[2].params.get('sort'), 'artist');
    h.pending[2].resolve(payload('Sorted result', 400));
    await waitFor(() => h.doc.getElementById('tracks-list').textContent.includes('Sorted result'));
    h.pending[1].resolve(payload('Old second page', 1000));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(!h.doc.getElementById('tracks-list').textContent.includes('Old second page'));
    assert.equal(h.doc.getElementById('tracks-count').textContent, '400 tracks');
    assert.equal(h.doc.getElementById('tracks-page-info').textContent, 'Page 1 / 2');
  } finally { h.dom.window.close(); }
}

async function testNavigationDiscardsPendingWork() {
  const h = await createHarness();
  try {
    h.search('pending');
    await waitFor(() => h.pending.length === 2);
    const oldList = h.doc.getElementById('tracks-list');
    await h.window.navigate('home');
    const returnToTracks = h.window.navigate('tracks');
    await waitFor(() => h.pending.length === 3);
    h.pending[2].resolve(payload('Fresh Tracks view'));
    await returnToTracks;
    h.pending[1].resolve(payload('Detached response'));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(!oldList.textContent.includes('Detached response'), 'Discard responses for detached view instances');
    assert.ok(h.doc.getElementById('tracks-list').textContent.includes('Fresh Tracks view'));

    h.search('debounced');
    await h.window.navigate('home');
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(h.pending.length, 3, 'A detached view must not start a debounced request');
  } finally { h.dom.window.close(); }
}

async function testSortDuringDebounceUsesLatestQuery() {
  const h = await createHarness();
  try {
    h.search('new query');
    const sort = h.doc.getElementById('track-sort');
    sort.value = 'recent';
    sort.dispatchEvent(new h.window.Event('change', { bubbles: true }));
    await waitFor(() => h.pending.length === 2);
    assert.equal(h.pending[1].params.get('search'), 'new query');
    assert.equal(h.pending[1].params.get('sort'), 'recent');
    h.pending[1].resolve(payload('Recent query result'));
    await waitFor(() => h.doc.getElementById('tracks-list').textContent.includes('Recent query result'));
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(h.pending.length, 2, 'Sorting consumes the pending debounce without a duplicate request');
  } finally { h.dom.window.close(); }
}

await testLatestSearchWins();
await testTypingInvalidatesBeforeDebounce();
await testSortAndPageShareRequestOrdering();
await testNavigationDiscardsPendingWork();
await testSortDuringDebounceUsesLatestQuery();
console.log('frontend_tracks_request_order_check=OK');
