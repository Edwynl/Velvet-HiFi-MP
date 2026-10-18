import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<main id="main"><div id="view-container"></div></main>', {
  url: 'http://localhost/', runScripts: 'outside-only',
});
const { window } = dom;
let albums = [
  { id: 11, artist_id: 7, artist_name: '方大同', title: 'Soulboy', track_count: 14, artist_role: 'primary' },
  { id: 22, artist_id: 8, artist_name: '薛凱琪', title: 'Filicious', track_count: 10, artist_role: 'collaboration' },
];
const navigation = [];
Object.assign(window, {
  UI: new Proxy({}, { get: (_, id) => window.document.getElementById(id) }),
  state: { view: 'artist' },
  _getCachedData: () => null,
  _cacheData: () => {},
  getThumbSize: () => 'small',
  escHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char])),
  navigate: (view, params) => navigation.push({ view, params }),
  api: async path => {
    if (path === '/artists/7') return { id: 7, name: '方大同' };
    if (path === '/artists/7/albums') return structuredClone(albums);
    if (path === '/artists/7/enrich-bio') return {};
    throw new Error(`Unexpected request: ${path}`);
  },
});
window.eval(readFileSync('static/js/library-cards.js', 'utf8'));
window.eval(readFileSync('static/js/library-views.js', 'utf8'));
const view = window.document.getElementById('view-container');
const render = () => window.renderArtist(view, { id: '7' });

await render();
assert.equal(view.querySelectorAll('#artist-albums-grid .card').length, 1);
assert.equal(view.querySelector('#artist-albums-grid .card-title').textContent.trim(), 'Soulboy');
assert.equal(view.querySelectorAll('#artist-collaborations-grid .card').length, 1);
assert.ok(view.querySelector('#artist-collaborations-grid .card-sub').textContent.includes('薛凱琪'));
assert.match(view.querySelector('.artist-hero-info .section-sub:last-child').textContent, /1 albums · 14 tracks · 1 collaborations/);
view.querySelector('#artist-collaborations-grid .card').click();
assert.deepEqual(structuredClone(navigation.pop()), { view: 'album', params: { id: 22 } });

// The artist_id fallback also works against an older server without role fields.
albums.forEach(album => delete album.artist_role);
await render();
assert.equal(view.querySelectorAll('#artist-albums-grid .card').length, 1);
assert.equal(view.querySelectorAll('#artist-collaborations-grid .card').length, 1);
albums = albums.filter(album => album.artist_id === 8);
await render();
assert.equal(view.querySelectorAll('#artist-albums-grid .card').length, 0);
assert.equal(view.querySelectorAll('#artist-collaborations-grid .card').length, 1);
albums = [{ id: 11, artist_id: 7, title: 'Soulboy', track_count: 14 }];
await render();
assert.equal(view.querySelector('#artist-collaborations-grid'), null);
assert.ok(view.querySelector('#artist-albums-grid .card-sub').textContent.includes('方大同'));
dom.window.close();
console.log('frontend_artist_discography=OK');
