import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';
const wait = async predicate => {
  for (let n = 0; n < 200; n++) { if (predicate()) return; await new Promise(r => setTimeout(r, 10)); }
  throw new Error('Artist navigation did not settle');
};
for (const bio of ['Saved <biography>', '', null]) {
  const requests = [];
  const album = { id: 11, title: 'Listening Album', artist_id: 7, artist_name: 'Listening Artist', tracks: [] };
  const dom = new JSDOM(inlineFrontendModules(readFileSync('static/index.html', 'utf8')).replace(/<link[^>]+>/g, ''), {
    url: 'http://localhost/#artists', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = async (url, opts = {}) => {
        const p = new URL(url, 'http://localhost').pathname;
        const method = (opts.method || 'GET').toUpperCase(); requests.push({ p, method });
        let data = {}, status = 200;
        if (p === '/api/security/status') data = { enabled: false };
        else if (p === '/api/artists/page') data = { artists: [{ id: 7, name: 'Listening Artist' }], total: 1 };
        else if (p === '/api/artists/7' && method === 'GET') data = { id: 7, name: 'Listening Artist', bio };
        else if (p === '/api/artists/7/albums') data = [album];
        else if (p === '/api/albums/11') data = album;
        else if (p === '/api/albums/11/credits') data = { composers: [], works: [], performers: [], conductors: [], ensembles: [] };
        else if (p === '/api/dsp/profiles') data = [];
        else if (p === '/api/stats') data = { albums: 1, artists: 1, tracks: 0 };
        else if (p === '/api/scan/status') data = { running: false };
        if (method !== 'GET' && p.startsWith('/api/artists/')) { status = 403; data = { code: 'admin_password_required', error: 'Administrator password required' }; }
        const response = { ok: status === 200, status, headers: { get: () => 'application/json' }, async json() { return structuredClone(data); }, async text() { return JSON.stringify(data); } };
        response.clone = () => response;
        return response;
      };
      w.matchMedia = q => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} });
      w.scrollTo = () => {}; w.alert = () => {}; w.confirm = () => true;
      w.HTMLMediaElement.prototype.load = () => {};
      w.HTMLMediaElement.prototype.pause = () => {};
      w.HTMLMediaElement.prototype.play = () => Promise.resolve();
    },
  });
  try {
    await wait(() => dom.window.document.querySelector('#artists-grid .card'));
    dom.window.document.querySelector('#artists-grid .card').click();
    await wait(() => dom.window.document.querySelector('#artist-albums-grid .card'));
    assert.equal(dom.window.document.querySelector('#admin-verification'), null, 'opening an artist is read-only');
    assert.equal(requests.filter(r => r.p.includes('enrich-bio')).length, 0, 'browsing must not submit biography updates');
    assert.equal(dom.window.document.querySelector('#artist-bio').textContent, bio || '');
    assert.equal(dom.window.document.querySelector('#artist-bio biography'), null, 'cached biography is escaped');
    for (let n = 0; n < 3; n++) {
      dom.window.document.querySelector('#artist-albums-grid .card').click();
      await wait(() => dom.window.document.querySelector('#play-all-btn'));
      dom.window.document.querySelector('.back-btn').click();
      await wait(() => dom.window.document.querySelector('#artist-albums-grid .card'));
      assert.equal(dom.window.document.querySelector('#admin-verification'), null, 'Back must not ask for administrator approval');
    }
    assert.equal(requests.filter(r => r.method !== 'GET' && r.p.startsWith('/api/artists/')).length, 0);
    if (typeof dom.window.managementFetch === 'function') {
      dom.window.document.querySelector('#refresh-artist-btn').click();
      await wait(() => dom.window.document.querySelector('#admin-verification'));
      assert.equal(requests.filter(r => r.p.endsWith('/refresh') && r.method === 'POST').length, 1, 'explicit metadata changes remain protected');
      dom.window.document.querySelector('#admin-verification-cancel').click();
    }
  } finally { await new Promise(setImmediate); dom.window.close(); }
}
console.log('frontend_artist_readonly_navigation=OK (3 biography cases, 9 Back traversals)');
