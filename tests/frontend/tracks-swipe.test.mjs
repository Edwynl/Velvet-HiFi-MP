import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<main id="main"><div id="view-container"></div></main>', {
  url: 'http://localhost/#tracks', runScripts: 'outside-only',
});
const { window } = dom;
const tracks = [
  { id: 11, title: 'First <song>', artist_name: 'Ada', album_title: 'X', duration: 180, format: 'FLAC' },
  { id: 22, title: 'Second song', artist_name: 'Bo', album_title: 'Y', duration: 200, format: 'FLAC' },
];
let plays = 0;
let picked = null;
let clock = 10000;
window.performance.now = () => clock;
Object.assign(window, {
  UI: new Proxy({}, { get: (_, id) => window.document.getElementById(id) }),
  escHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char])),
  fmt: () => '3:00',
  fmtSR: () => '',
  getTopNavHtml: () => '',
  setupSearch: () => {},
  state: { view: 'tracks' },
  playTrack: () => { plays++; },
  showPlaylistPicker: track => { picked = track; },
  api: async () => ({ tracks: structuredClone(tracks), total: tracks.length }),
});
window.eval(readFileSync('static/js/library-cards.js', 'utf8'));
window.eval(readFileSync('static/js/library-views.js', 'utf8'));
const view = window.document.getElementById('view-container');
await window.renderTracks(view);

// The ＋ button is gone. It used to claim a sixth grid column, so the row
// hung in the dark with an empty track to the right of the duration.
assert.equal(view.querySelector('.track-add-btn'), null, 'Tracks page has no ＋ button');
assert.equal(view.querySelectorAll('.track-item').length, 2);
// Swiping the row left is the way into the playlist picker, the same gesture
// the album and playlist pages already use.
assert.equal(view.querySelectorAll('.track-swipe-row').length, 2);
assert.equal(view.querySelectorAll('.album-track-add').length, 2);

const row = view.querySelector('.track-item');
row.click();
assert.equal(plays, 1, 'A normal tap plays');

const pointer = (target, type, x, y = 0) => {
  const event = new window.MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 });
  Object.defineProperty(event, 'pointerId', { value: 1 });
  target.dispatchEvent(event);
};
pointer(row, 'pointerdown', 180);
pointer(row, 'pointermove', 100);
pointer(row, 'pointerup', 100);
assert.ok(row.parentElement.classList.contains('is-open'), 'Swiping left reveals the action');
row.parentElement.querySelector('button').click();
assert.equal(picked?.id, 11, 'The action opens the playlist picker for that track');
assert.equal(plays, 1, 'Opening the picker does not play');

// A vertical scroll must not reveal the action.
const second = view.querySelectorAll('.track-item')[1];
pointer(second, 'pointerdown', 180, 0);
pointer(second, 'pointermove', 176, 60);
pointer(second, 'pointerup', 176, 60);
assert.ok(!second.parentElement.classList.contains('is-open'), 'Vertical scrolling does not reveal the action');

// The keyboard gets there too.
clock += 500;
row.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowLeft' }));
assert.ok(row.parentElement.classList.contains('is-open'), 'ArrowLeft reveals the action');
row.parentElement.querySelector('button').click();
assert.equal(picked?.id, 11);

dom.window.close();
console.log('frontend_tracks_swipe=OK');
