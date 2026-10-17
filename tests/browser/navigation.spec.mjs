import { test, expect } from '@playwright/test';
import { prepare, go, albumsPage } from './helpers.mjs';
test.beforeEach(async ({ page }) => { await prepare(page); });

test('late Albums/Home, Album A/B, Settings/Albums and Back responses cannot overwrite the destination', async ({ page }) => {
  await page.goto('/#home'); await expect(page.locator('.home-hero')).toBeVisible();
  for (const [path, first, params, destination, destParams, selector] of [
    ['/api/albums/page*', 'albums', {}, 'home', {}, '.home-hero'],
    ['/api/albums/1', 'album', { id: 1 }, 'album', { id: 2 }, '.album-hero-title'],
    ['/api/config', 'settings', {}, 'albums', {}, '#albums-grid'],
  ]) {
    let release; const gate = new Promise(r => { release = r; }); let entered;
    const started = new Promise(r => { entered = r; });
    await page.route('**' + path, async route => {
      entered(); await gate;
      try { await route.continue(); } catch { /* cancellation is expected */ }
    });
    await page.evaluate(({ first, params }) => { window.__pendingView = navigate(first, params); }, { first, params });
    await started;
    await go(page, destination, destParams);
    release(); await page.evaluate(() => window.__pendingView);
    await expect(page.locator('#view-container')).toHaveClass('view-' + destination);
    await expect(page.locator(selector)).toBeVisible();
    if (destination === 'album') await expect(page.locator(selector)).toContainText('Album 002');
    await page.unroute('**' + path);
  }
  await go(page, 'home');
  await page.route('**/api/albums/page*', async r => { await new Promise(resolve => setTimeout(resolve, 450)); try { await r.continue(); } catch {} });
  await page.evaluate(() => { window.__pendingView = navigate('albums'); });
  await page.goBack(); await expect(page.locator('.home-hero')).toBeVisible();
  await page.evaluate(() => window.__pendingView);
  await expect(page.locator('#view-container')).toHaveClass('view-home');
});

test('delayed native Back causes exactly one traversal without the old 220ms fallback', async ({ page }) => {
  await albumsPage(page);
  await page.locator('#albums-grid .card-open').first().click();
  await expect(page.locator('.back-btn')).toBeVisible();
  await page.evaluate(() => {
    const nativeBack = history.back.bind(history);
    window.__backCalls = 0;
    // An intentional 600ms native-event delay reproduces slow Safari history.
    history.back = () => { window.__backCalls++; setTimeout(nativeBack, 600); };
  });
  await page.locator('.back-btn').click();
  await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
  expect(await page.evaluate(() => window.__backCalls)).toBe(1);
  await page.goForward(); await expect(page.locator('.album-hero')).toBeVisible();
  await page.goBack(); await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
});

test('a successful catalog mutation refetches while preserving page, scroll and updated data', async ({ page }) => {
  await albumsPage(page);
  await page.locator('#albums-grid .card').nth(17).scrollIntoViewIfNeeded();
  const scroll = await page.locator('#main').evaluate(e => e.scrollTop);
  await page.locator('#albums-grid .card-open').nth(17).click();
  let queries = 0;
  await page.route('**/api/albums/page*', async r => {
    queries++; const response = await r.fetch(); const data = await response.json();
    data.albums[0].title = 'Fresh catalog title'; await r.fulfill({ json: data });
  });
  await page.evaluate(async () => { await api('/albums/198/favorite', { method: 'POST' }); });
  await page.locator('.back-btn').click();
  await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
  await expect(page.locator('#albums-grid .card-title').first()).toHaveText('Fresh catalog title');
  await expect.poll(() => page.locator('#main').evaluate(e => e.scrollTop)).toBe(scroll);
  expect(queries).toBe(1);
});

test('search, Home and artist origins return once; a direct album link falls back to Albums', async ({ page }) => {
  await page.goto('/#home'); await expect(page.locator('.home-hero')).toBeVisible();
  for (const [view, params, selector] of [['home', {}, '.home-hero'], ['search', { q: 'Movement' }, '.track-item'], ['artists', {}, '#artists-grid'], ['artist', { id: 1 }, '#artist-albums-grid']]) {
    await go(page, view, params);
    await go(page, 'album', { id: 1 });
    await page.locator('.back-btn').click();
    await expect(page.locator('#view-container')).toHaveClass('view-' + view);
    await expect(page.locator(selector).first()).toBeVisible();
    if (view === 'search') { await page.reload(); await expect(page.locator('.track-item').first()).toBeVisible(); }
  }
  await page.goto('/#album/1'); await expect(page.locator('.back-btn')).toBeVisible();
  await page.locator('.back-btn').click(); await expect(page.locator('#page-info')).toHaveText('Page 1 / 11');
});

test('Artists GET failure has a local retry, preserves playback and never reloads the application', async ({ page }) => {
  await page.goto('/#album/1'); await page.locator('#play-all-btn').click();
  let calls = 0;
  await page.route('**/api/artists/page*', async r => { calls++; if (calls === 1) await r.fulfill({ status: 503, json: { error: 'test outage' } }); else await r.continue(); });
  await go(page, 'artists');
  await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
  const routeId = await page.evaluate(() => history.state.id);
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(page.locator('#artists-grid .card')).toHaveCount(1);
  expect(await page.evaluate(() => history.state.id)).toBe(routeId);
  await expect(page.locator('#player-title')).toHaveText('Movement 1');
});

test('137 albums restore the third page; search survives a catalog invalidation and reload', async ({ page }) => {
  await page.route('**/api/albums/page*', async r => {
    const response = await r.fetch(); const data = await response.json();
    const offset = Number(new URL(r.request().url()).searchParams.get('offset') || 0);
    data.total = 137; data.albums = data.albums.slice(0, Math.max(0, 137 - offset));
    await r.fulfill({ json: data });
  });
  await page.goto('/#albums');
  for (let n = 2; n <= 3; n++) { await page.locator('#next-page-btn').click(); await expect(page.locator('#page-info')).toHaveText(`Page ${n} / 3`); }
  await page.locator('#albums-grid .card-open').nth(16).click();
  await page.evaluate(async () => { await api('/stream-ticket', { method: 'POST' }); });
  await page.locator('.back-btn').click(); await expect(page.locator('#page-info')).toHaveText('Page 3 / 3');
  await expect(page.locator('#albums-grid .card')).toHaveCount(17);
  await page.locator('#search-input').fill('137');
  await expect(page.locator('#albums-grid')).toContainText('Movement 1');
  await page.locator('#albums-grid .history-item').first().click();
  await page.evaluate(async () => { await api('/albums/1/favorite', { method: 'POST' }); });
  await page.locator('.back-btn').click();
  await expect(page.locator('#search-input')).toHaveValue('137');
  await expect(page.locator('#albums-grid')).toContainText('Movement 1');
  await page.reload(); await expect(page.locator('#search-input')).toHaveValue('137');
});

test('desktop resize and keyboard access preserve the catalog and semantic card controls', async ({ page }) => {
  await albumsPage(page);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
  const open = page.locator('#albums-grid .card-open').first(); await open.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.album-hero')).toBeVisible();
  await page.locator('.back-btn').focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
});

test('destructive confirmation traps focus and Escape cancels without a mutation', async ({ page }) => {
  await page.goto('/#album/1'); await expect(page.locator('.album-hero')).toBeVisible();
  let deleted = false; page.on('request', r => { if (r.method() === 'DELETE') deleted = true; });
  await page.evaluate(() => document.getElementById('del-album-btn').click());
  await expect(page.locator('#modal-container')).toHaveAttribute('aria-modal', 'true');
  await expect(page.locator('#modal-cancel')).toBeFocused();
  await page.locator('#modal-confirm').focus(); await page.keyboard.press('Tab');
  await expect(page.locator('#modal-container :focus')).toHaveCount(1);
  await page.keyboard.press('Escape'); await expect(page.locator('#modal-container')).not.toHaveClass(/open/);
  expect(deleted).toBeFalsy();
});

test('a restored offset below the first lazy batch is not clamped on reload', async ({ page }) => {
  await albumsPage(page);
  for (let i = 0; i < 2; i++) {
    await page.locator('#main').evaluate(e => { e.scrollTop = e.scrollHeight; });
    await expect.poll(() => page.locator('#albums-grid .card').count()).toBeGreaterThanOrEqual(i ? 60 : 48);
  }
  await page.locator('#albums-grid .card').nth(53).scrollIntoViewIfNeeded();
  const scroll = await page.locator('#main').evaluate(e => e.scrollTop);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await page.reload(); await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
  await expect.poll(() => page.locator('#main').evaluate(e => e.scrollTop)).toBe(scroll);
  await expect(page.locator('#albums-grid .card')).toHaveCount(60);
});

test('a pending Settings mutation completes once after leaving without replay or stale errors', async ({ page }) => {
  await page.goto('/#settings'); await expect(page.locator('#library-add-form')).toBeVisible();
  let release, entered; const held = new Promise(r => { release = r; }); const started = new Promise(r => { entered = r; });
  let mutations = 0;
  await page.route('**/api/config/libraries', async r => {
    mutations++; entered(); await held;
    await r.fulfill({ json: { success: true, libraries: [{ id: 1, path: 'C:/QA/Music' }, { id: 2, path: 'C:/QA/New' }] } });
  });
  await page.locator('#music-dir-input').fill('C:/QA/New');
  await page.locator('#add-lib-btn').click(); await started;
  await go(page, 'albums'); release();
  await expect(page.locator('#albums-grid .card').first()).toBeVisible();
  await expect(page.locator('#toast')).not.toContainText(/View superseded|Network error|Error:/);
  expect(mutations).toBe(1);
});
