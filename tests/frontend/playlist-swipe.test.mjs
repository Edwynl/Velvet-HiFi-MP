import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<main id="main"><div id="view-container"></div></main>', {
  url: 'http://localhost/#playlist/1', runScripts: 'outside-only',
});
const { window } = dom;
let tracks = [{ id: 11, title: 'First <song>' }, { id: 22, title: 'Second song' }];
let confirmation, clock = 10000, plays = 0, deletes = 0, fail = false;
window.performance.now = () => clock;
Object.assign(window, {
  UI: new Proxy({}, { get: (_, id) => window.document.getElementById(id) }),
  escHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char])),
  fmt: () => '3:00', fmtSR: () => '', playTrack: () => { plays++; },
  showToast: () => {}, showConfirm: options => { confirmation = options; },
  api: async (path, options = {}) => {
    if (options.method === 'DELETE') {
      deletes++;
      if (fail) return null;
      tracks = tracks.filter(track => String(track.id) !== path.split('/').at(-1));
      return { success: true };
    }
    return { id: 1, name: 'My <mix>', tracks: structuredClone(tracks) };
  },
});
window.eval(readFileSync('static/js/library-cards.js', 'utf8'));
window.eval(readFileSync('static/js/library-views.js', 'utf8'));
const view = window.document.getElementById('view-container');
await window.renderPlaylist(view, { id: 1 });
assert.equal(view.querySelector('.track-add-btn'), null);
assert.equal(view.querySelectorAll('.playlist-track-delete').length, 2);
assert.ok(view.querySelector('.playlist-back-btn').classList.contains('btn-ghost'));
assert.equal(view.querySelector('#rename-playlist-btn').parentElement.className, 'playlist-toolbar-actions');
const pointer = (row, type, x, y = 0) => {
  const event = new window.MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 });
  Object.defineProperty(event, 'pointerId', { value: 1 });
  row.dispatchEvent(event);
};
const swipe = row => {
  pointer(row, 'pointerdown', 180);
  pointer(row, 'pointermove', 100);
  pointer(row, 'pointerup', 100);
};
let row = view.querySelector('.track-item');
row.click();
assert.equal(plays, 1, 'A normal tap plays');
swipe(row);
assert.ok(row.parentElement.classList.contains('is-open'));
row.click();
assert.equal(plays, 1, 'The synthetic click after a swipe never plays');
row.parentElement.querySelector('button').click();
assert.equal(deletes, 0, 'Opening the confirmation does not delete');
assert.ok(confirmation.body.includes('First &lt;song&gt;'));
assert.equal(confirmation.confirmText, 'Confirm');
// Cancel means leaving the callback uncalled. The track remains in the list.
assert.equal(view.querySelectorAll('.track-item').length, 2);
const second = view.querySelectorAll('.track-item')[1];
swipe(second);
assert.ok(!row.parentElement.classList.contains('is-open'), 'Only one action stays open');
clock += 500;
row.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowLeft' }));
row.parentElement.querySelector('button').click();
await confirmation.onConfirm();
assert.equal(deletes, 1);
assert.equal(view.querySelectorAll('.track-item').length, 1);
assert.ok(view.textContent.includes('1 tracks'));
row = view.querySelector('.track-item');
pointer(row, 'pointerdown', 180, 0);
pointer(row, 'pointermove', 176, 60);
pointer(row, 'pointerup', 176, 60);
assert.ok(!row.parentElement.classList.contains('is-open'), 'Vertical scrolling does not reveal Delete');
swipe(row);
row.parentElement.querySelector('button').click();
fail = true;
await confirmation.onConfirm();
assert.equal(view.querySelectorAll('.track-item').length, 1, 'Failed removal retains the track');
assert.equal(row.parentElement.querySelector('button').disabled, false);
fail = false;
await confirmation.onConfirm();
assert.equal(view.querySelectorAll('.track-item').length, 0);
assert.ok(view.textContent.includes('This playlist is empty'));
dom.window.close();
console.log('frontend_playlist_swipe=OK');
