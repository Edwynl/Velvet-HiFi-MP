import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

const albums = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1, title: `Album ${i + 1}`, artist_id: i % 8 + 1, artist_name: `Artist ${i % 8 + 1}`, year: 2026,
}));
const artists = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, name: `Artist ${i + 1}`, album_count: 3 }));
const response = data => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, async text() { return JSON.stringify(data); } });
const waitFor = async predicate => {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Timed out waiting for Home');
};

async function harness(homeData, { measuredLayout = false } = {}) {
  const requests = [];
  const html = inlineFrontendModules(readFileSync('static/index.html', 'utf8')).replace(/<link[^>]+>/g, '');
  const dom = new JSDOM(html, {
    url: 'http://localhost/#home', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      if (measuredLayout) {
        Object.defineProperty(window.HTMLElement.prototype, 'clientWidth', {
          get() { return this.classList.contains('home-coverflow') ? 430 : 0; },
        });
        Object.defineProperty(window.HTMLElement.prototype, 'offsetWidth', {
          get() { return this.classList.contains('home-flow-slot') ? 224 : 0; },
        });
        Object.defineProperty(window.HTMLElement.prototype, 'offsetLeft', {
          get() { return this.classList.contains('home-flow-slot') ? 103 + [...this.parentElement.children].indexOf(this) * 112 : 0; },
        });
        window.HTMLElement.prototype.scrollTo = function({ left, behavior }) {
          this._lastScroll = { left, behavior };
          if (behavior !== 'smooth') {
            this.scrollLeft = left;
            this.dispatchEvent(new window.Event('scroll'));
          }
        };
      }
      window.fetch = async url => {
        const u = new URL(url, 'http://localhost');
        requests.push(u);
        if (u.pathname === '/api/home') return response(await homeData);
        if (u.pathname === '/api/recently-added') return response(albums);
        if (u.pathname === '/api/stats') return response({ albums: 50, artists: 10, tracks: 500 });
        if (u.pathname === '/api/dsp/profiles') return response([]);
        if (u.pathname === '/api/albums/1') return response({ ...albums[0], tracks: [] });
        if (u.pathname === '/api/albums/1/credits') return response({});
        return response({});
      };
      window.scrollTo = () => {};
      window.matchMedia = query => ({ media: query, matches: false, addEventListener() {}, removeEventListener() {} });
      window.HTMLMediaElement.prototype.play = () => Promise.resolve();
      window.HTMLMediaElement.prototype.pause = () => {};
      window.HTMLMediaElement.prototype.load = () => {};
      window.navigator.mediaSession = { setActionHandler() {} };
    },
  });
  await waitFor(() => dom.window.document.getElementById('recent-grid')?.getAttribute('aria-busy') === 'false');
  return { dom, window: dom.window, doc: dom.window.document, requests };
}

const h = await harness({
  recently_opened: albums, frequent_artists: artists,
  for_you: albums.slice(0, 10).map(a => ({ ...a, id: a.id + 100 })),
  global_picks: albums.slice(0, 4).map(a => ({ ...a, id: a.id + 200 })),
});
try {
  await waitFor(() => h.doc.getElementById('home-recommendations-grid').getAttribute('aria-busy') === 'false');
  assert.equal(h.doc.querySelectorAll('#recent-grid .card').length, 24);
  assert.equal(h.doc.querySelectorAll('#home-opened-grid .card').length, 12);
  assert.equal(h.doc.querySelectorAll('#home-artists-grid .card').length, 8);
  assert.equal(h.doc.querySelectorAll('#home-recommendations-grid .card').length, 12);
  assert.equal(h.doc.querySelectorAll('.view-home .track-item').length, 0);
  assert.equal(h.doc.querySelectorAll('.view-home .card-play-btn').length, 0, 'Home covers have no overlaid play buttons');
  assert.equal(h.doc.querySelectorAll('#recent-grid .card-art img').length, 24, 'Reflections reuse loaded covers without image copies');
  assert.equal(h.doc.querySelectorAll('#recent-grid .home-cover-reflection').length, 24);
  assert.equal(h.requests.find(u => u.pathname === '/api/recently-added').searchParams.get('limit'), '24');
  assert.equal(h.doc.querySelector('.home-quick-links'), null, 'Remove the four redundant shortcut buttons');
  assert.ok(!h.requests.some(u => u.pathname === '/api/discover'), 'Home never downloads Discover track sections');
  assert.equal(h.doc.getElementById('home-artists-grid-title').textContent, 'Your Artists');
  assert.equal(h.doc.querySelectorAll('.home-coverflow').length, 3, 'Only album shelves use cover flow');
  assert.equal(h.doc.getElementById('home-artists-grid').classList.contains('home-coverflow'), false);
  const rail = h.doc.getElementById('recent-grid');
  const flowControls = rail.nextElementSibling;
  const selectedTitle = () => rail.querySelector('.is-current .card-title').textContent;
  assert.equal(selectedTitle(), 'Album 1', 'The newest album starts in the spotlight');
  assert.equal(flowControls.querySelector('.home-flow-position').textContent, '01 / 24');
  assert.equal(flowControls.querySelector('button'), null, 'Carousel has no previous/next buttons');
  assert.equal(h.doc.querySelector('#recent-grid-title').parentElement.querySelector('.discover-section-sub'), null);
  rail.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(selectedTitle(), 'Album 2');
  rail.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  assert.equal(selectedTitle(), 'Album 24');
  rail.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
  assert.equal(selectedTitle(), 'Album 1');
  rail.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  const firstAlbum = [...rail.querySelectorAll('.card')].find(c => c.querySelector('.card-title').textContent === 'Album 1');
  firstAlbum.click();
  assert.equal(selectedTitle(), 'Album 1', 'A side-cover tap centers it');
  assert.equal(h.window.eval('state.view'), 'home', 'The first side-cover tap does not navigate');

  for (const [hour, period] of [[0, 'Evening'], [4, 'Evening'], [5, 'Morning'], [11, 'Morning'], [12, 'Afternoon'], [17, 'Afternoon'], [18, 'Evening'], [23, 'Evening']]) {
    const date = new h.window.Date(); date.setHours(hour);
    assert.equal(h.window.homeGreeting(date), `Good ${period}, Edwyn`);
  }
  const originalGreeting = h.window.homeGreeting;
  h.window.homeGreeting = () => 'Good Evening, Edwyn';
  for (const event of ['pageshow', 'visibilitychange']) {
    h.doc.getElementById('home-greeting').textContent = 'Good morning.';
    (event === 'pageshow' ? h.window : h.doc).dispatchEvent(new h.window.Event(event));
    assert.equal(h.doc.getElementById('home-greeting').textContent, 'Good Evening, Edwyn');
  }
  h.window.homeGreeting = originalGreeting;

  await h.window.navigate('album', { id: 1 });
  rail.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  assert.equal(selectedTitle(), 'Album 1', 'Detached rails no longer process keyboard events');
  let activity = h.window.readHomeActivity();
  assert.equal(activity.albums[0].id, 1, 'Actual album navigation records a visit');
  assert.equal(activity.artists[0].id, 1);
  for (let id = 2; id <= 20; id++) h.window.recordHomeVisit('album', { id, artist_id: 2 });
  h.window.recordHomeVisit('album', { id: 18, artist_id: 1 });
  activity = h.window.readHomeActivity();
  assert.equal(activity.albums.length, 12);
  assert.equal(activity.albums[0].id, 18);
  assert.equal(new Set(activity.albums.map(a => a.id)).size, 12);
  assert.equal(activity.artists[0].id, 2, 'Artist shelf uses visit frequency, not just recency');
  await h.window.navigate('home');
  const request = h.requests.filter(u => u.pathname === '/api/home').at(-1);
  assert.equal(request.searchParams.get('album_ids').split(',')[0], '18');
  assert.equal(request.searchParams.get('artist_ids').split(',')[0], '2');
  h.window.localStorage.setItem('velvet:home-activity:v1', 'corrupt');
  assert.equal(h.window.readHomeActivity().albums.length, 0);
} finally { h.dom.window.close(); }

const empty = await harness(null);
try {
  await waitFor(() => empty.doc.getElementById('home-opened-grid').getAttribute('aria-busy') === 'false');
  assert.ok(empty.doc.getElementById('home-opened-grid').textContent.includes('Open an album'));
  assert.equal(empty.doc.querySelectorAll('#recent-grid .card').length, 24, 'Recent uploads survive unavailable recommendations');
} finally { empty.dom.window.close(); }

const smooth = await harness({}, { measuredLayout: true });
try {
  const rail = smooth.doc.getElementById('recent-grid');
  const next = () => rail.dispatchEvent(new smooth.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  next();
  assert.equal(rail._lastScroll.behavior, 'smooth');
  assert.equal(rail.querySelector('.is-current .card-title').textContent, 'Album 1',
    'Navigation does not jump cover transforms to the endpoint before scrolling');
  rail.scrollLeft = 280; // Halfway from display index 2 (left=224) to index 3.
  rail.dispatchEvent(new smooth.window.Event('scroll'));
  await waitFor(() => rail.querySelector('.is-current .card-title').textContent === 'Album 2');
  const first = [...rail.querySelectorAll('.card')].find(c => c.querySelector('.card-title').textContent === 'Album 1');
  const angle = Math.abs(parseFloat(first.style.getPropertyValue('--flow-angle')));
  assert.ok(angle > 0 && angle < 32, 'New Release turns gently with the actual scroll position');
  assert.equal(Number(first.style.getPropertyValue('--flow-caption')), .5, 'Captions crossfade through the midpoint');
  next();
  next();
  assert.equal(rail._lastScroll.left, 560, 'Rapid navigation accumulates against the pending destination');
  rail.scrollLeft = rail._lastScroll.left;
  rail.dispatchEvent(new smooth.window.Event('scroll'));
  await waitFor(() => rail.querySelector('.is-current .card-title').textContent === 'Album 4');
  const image = first.querySelector('.card-art img');
  image.dispatchEvent(new smooth.window.Event('load'));
  assert.ok(first.querySelector('.home-cover-reflection').style.getPropertyValue('--reflection-image').includes('/api/covers/1/thumb'),
    'The explicit reflection follows the successfully loaded cover');
  const pointer = (type, x, time) => {
    const event = new smooth.window.MouseEvent(type, { clientX: x, button: 0, bubbles: true, cancelable: true });
    Object.defineProperties(event, { pointerType: { value: 'mouse' }, pointerId: { value: 1 }, timeStamp: { value: time } });
    rail.dispatchEvent(event);
  };
  pointer('pointerdown', 300, 100);
  pointer('pointermove', 297, 110);
  assert.equal(rail.classList.contains('is-dragging'), false, 'Small mouse movements remain clicks');
  pointer('pointermove', 220, 130);
  const releaseLeft = rail.scrollLeft;
  assert.equal(rail.classList.contains('is-dragging'), true);
  pointer('pointerup', 220, 140);
  assert.equal(rail.classList.contains('is-dragging'), false);
  assert.ok(rail._lastScroll.left > releaseLeft + 112, 'Mouse flick coasts across multiple covers');
  first.click();
  assert.equal(smooth.window.eval('state.view'), 'home', 'Releasing a drag never opens an album');
} finally { smooth.dom.window.close(); }

console.log('frontend_home_shelves_check=OK');
