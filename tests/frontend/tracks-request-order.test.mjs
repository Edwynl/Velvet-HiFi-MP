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
  // Sorting and paging are the only per-page request triggers left: the shared
  // topbar owns searching, so typing navigates to the unified search view.
  const sort = (value) => {
    const el = doc.getElementById('track-sort');
    el.value = value;
    el.dispatchEvent(new window.Event('change', { bubbles: true }));
  };
  const nextPage = () => doc.getElementById('tracks-next-btn').click();
  return { dom, window, doc, pending, sort, nextPage };
}

async function testSortAndPageShareRequestOrdering() {
  const h = await createHarness();
  try {
    h.nextPage();
    await waitFor(() => h.pending.length === 2);
    assert.equal(h.pending[1].params.get('offset'), '200');
    h.sort('artist');
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

async function testSortDuringPageKeepsLatestOrdering() {
  const h = await createHarness();
  try {
    h.nextPage();
    await waitFor(() => h.pending.length === 2);
    h.sort('recent');
    await waitFor(() => h.pending.length === 3);
    assert.equal(h.pending[2].params.get('sort'), 'recent');
    assert.equal(h.pending[2].params.get('offset'), '0');
    h.pending[2].resolve(payload('Recent result', 40));
    await waitFor(() => h.doc.getElementById('tracks-list').textContent.includes('Recent result'));
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(h.pending.length, 3, 'A sort must not queue a duplicate request');
  } finally { h.dom.window.close(); }
}

async function testNavigationDiscardsPendingWork() {
  const h = await createHarness();
  try {
    h.sort('composer');
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
  } finally { h.dom.window.close(); }
}

async function testTracksPageHasNoLocalSearchRequest() {
  const h = await createHarness();
  try {
    // The topbar input is shared. Typing must not fire a /tracks?search= call,
    // because that request form no longer exists on this page.
    const input = h.doc.getElementById('search-input');
    assert.ok(input, 'Tracks page keeps the shared topbar search field');
    input.value = 'local filter';
    input.dispatchEvent(new h.window.Event('input', { bubbles: true }));
    await waitFor(() => h.doc.querySelector('#global-search-results'));
    await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(h.pending.length, 1, 'Typing must not issue a local Tracks search request');
    assert.ok(!h.doc.getElementById('tracks-list'),
      'Typing routes to the unified search view instead of filtering this page');
  } finally { h.dom.window.close(); }
}

await testSortAndPageShareRequestOrdering();
await testSortDuringPageKeepsLatestOrdering();
await testNavigationDiscardsPendingWork();
await testTracksPageHasNoLocalSearchRequest();
console.log('frontend_tracks_request_order_check=OK');