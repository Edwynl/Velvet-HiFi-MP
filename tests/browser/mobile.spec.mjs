import { test, expect } from '@playwright/test';
import { prepare, go, albumsPage, shot, noOverflow } from './helpers.mjs';

for (const standalone of [false, true]) {
  const mode = standalone ? 'standalone-simulation' : 'browser';
  test.describe(mode, () => {
    test.beforeEach(async ({ page }) => { await prepare(page, standalone); });
    test('one Back preserves page 4, filters and card offset after five tracks and ticket/history POSTs', async ({ page }, info) => {
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      await albumsPage(page);
      // Explicitly persist filters and sort without changing the fixture's catalog size.
      await page.locator('#album-sort-select .v-select-trigger').click();
      await page.locator('#album-sort-select [data-value="year"]').click();
      await expect(page.locator('#page-info')).toHaveText('Page 1 / 11');
      await page.locator('#album-genre-select .v-select-trigger').click();
      await page.locator('#album-genre-select [data-value="Classical"]').click();
      await page.locator('#album-decade-select .v-select-trigger').click();
      await page.locator('#album-decade-select [data-value="2020s"]').click();
      await page.locator('#favorites-filter-btn').click();
      for (let i = 2; i <= 4; i++) { await page.locator('#next-page-btn').click(); await expect(page.locator('#page-info')).toHaveText(`Page ${i} / 11`); }
      const card = page.locator('#albums-grid .card').nth(17);
      await card.scrollIntoViewIfNeeded();
      const original = await page.locator('#main').evaluate(e => e.scrollTop);
      const offset = (await card.boundingBox()).y;
      const beforeGeometry = await card.evaluate(e => ({ cardTop: e.getBoundingClientRect().top, mainTop: document.getElementById('main').getBoundingClientRect().top, viewTop: document.getElementById('view-container').getBoundingClientRect().top, scroll: document.getElementById('main').scrollTop, windowScroll: scrollY, playerHeight: document.getElementById('player').getBoundingClientRect().height }));
      await shot(page, info, 'albums-page4', mode);
      await card.getByRole('button', { name: /^Open / }).click();
      await expect(page.locator('#play-all-btn')).toBeVisible();
      await shot(page, info, 'album', mode);
      const length = await page.evaluate(() => history.length);
      await page.locator('#play-all-btn').click();
      for (let i = 1; i < 5; i++) {
        await page.evaluate(() => document.getElementById('btn-next').click());
        await expect(page.locator('#player-title')).toHaveText(`Movement ${i + 1}`);
        await page.evaluate(async () => { await api('/stream-ticket', { method: 'POST' }); await api('/history', { method: 'POST', body: '{}' }); });
      }
      await page.evaluate(() => { document.getElementById('btn-play').click(); document.getElementById('btn-play').click(); });
      expect(await page.evaluate(() => history.length)).toBe(length);
      await page.evaluate(() => toggleMobilePlayer(true));
      await shot(page, info, 'fullscreen-player', mode);
      await page.getByRole('button', { name: 'Collapse player', exact: true }).click();
      await page.locator('.back-btn').click();
      await expect(page.locator('#page-info')).toHaveText('Page 4 / 11');
      await expect.poll(() => page.locator('#main').evaluate(e => e.scrollTop)).toBe(original);
      const afterGeometry = await page.locator('#albums-grid .card').nth(17).evaluate(e => ({ cardTop: e.getBoundingClientRect().top, mainTop: document.getElementById('main').getBoundingClientRect().top, viewTop: document.getElementById('view-container').getBoundingClientRect().top, scroll: document.getElementById('main').scrollTop, windowScroll: scrollY, playerHeight: document.getElementById('player').getBoundingClientRect().height }));
      await info.attach('back-geometry', { body: JSON.stringify({ beforeGeometry, afterGeometry }), contentType: 'application/json' });
      expect(Math.abs((await page.locator('#albums-grid .card').nth(17).boundingBox()).y - offset)).toBeLessThanOrEqual(1);
      expect(await page.evaluate(() => history.state.listState)).toMatchObject({ page: 4, sort: 'year', genre: 'Classical', decade: '2020s', favorites: true });
      await shot(page, info, 'back-restored', mode);
      await noOverflow(page); expect(errors).toEqual([]);
    });

    test('Settings re-entry retains working events; layouts, safe edges, queue, EQ, keyboard and rotation', async ({ page }, info) => {
      await page.goto('/#home'); await expect(page.locator('.home-hero')).toBeVisible();
      // Synthetic nonzero safe insets exercise the CSS; they do not emulate iOS chrome.
      await page.addStyleTag({ content: ':root{--safe-top:20px;--safe-right:24px;--safe-bottom:21px;--safe-left:24px;}' });
      await shot(page, info, 'home-safe-area', mode);
      if (await page.locator('#mobile-menu-btn').isVisible()) {
        await page.locator('#mobile-menu-btn').click(); await shot(page, info, 'drawer', mode);
        await page.locator('.nav-item[data-view="settings"]').click();
      }
      await go(page, 'settings'); await expect(page.locator('#library-add-form')).toBeVisible();
      await go(page, 'albums'); await go(page, 'settings');
      await page.locator('#dsp-new-custom-btn').click();
      await expect(page.locator('#dsp-editor-badge')).toHaveText('Blank');
      await shot(page, info, 'settings', mode); await noOverflow(page);
      const inputs = await page.locator('input:visible').evaluateAll(es => es.filter(e => !['range', 'checkbox'].includes(e.type)).map(e => parseFloat(getComputedStyle(e).fontSize)));
      expect(inputs.every(n => n >= 16)).toBeTruthy();
      const viewport = page.viewportSize();
      await page.setViewportSize({ width: viewport.width, height: Math.max(320, Math.floor(viewport.height * .6)) });
      await page.locator('#music-dir-input').focus();
      await shot(page, info, 'keyboard-viewport-simulation', mode);
      await page.locator('#music-dir-input').blur(); await page.setViewportSize(viewport);
      await go(page, 'remote'); await expect(page.locator('.section-title')).toHaveText('Remote Access');
      await shot(page, info, 'remote', mode); await noOverflow(page);
      await go(page, 'album', { id: 1 }); await page.locator('#play-all-btn').click();
      await page.evaluate(() => { toggleMobilePlayer(false); document.getElementById('btn-queue-toggle').click(); });
      await shot(page, info, 'queue', mode);
      await page.locator('#queue-close-btn').click();
      await page.evaluate(() => openGraphicEQ()); await shot(page, info, 'eq', mode);
      await page.evaluate(() => document.getElementById('eq-overlay').classList.remove('active'));
      await page.evaluate(() => { toggleMobilePlayer(true); toggleVUMeters(true); }); await shot(page, info, 'vu', mode);
      await page.evaluate(() => { toggleVUMeters(false); toggleMobilePlayer(false); });
      await page.setViewportSize({ width: viewport.height, height: viewport.width });
      await noOverflow(page); await shot(page, info, 'rotated', mode);
      await page.evaluate(() => showLoginModal('QA layout preview'));
      await shot(page, info, 'login', mode);
    });

    test('responsive artwork covers DPR demand and catalog state survives reload/page 10', async ({ page }, info) => {
      await albumsPage(page, 10);
      const cover = page.locator('#albums-grid .card-art img').first();
      await cover.scrollIntoViewIfNeeded();
      await expect.poll(() => cover.evaluate(e => e.naturalWidth)).toBeGreaterThan(0);
      const measurements = await cover.evaluate(async e => {
        // srcset density-corrects naturalWidth. Decode the selected resource
        // without srcset to measure the file's actual pixel dimensions.
        const raw = new Image(); raw.src = e.currentSrc; await raw.decode();
        return { css: e.getBoundingClientRect().width, pixels: raw.naturalWidth, dpr: devicePixelRatio, src: e.currentSrc };
      });
      expect(measurements.pixels).toBeGreaterThanOrEqual(Math.min(800, Math.floor(measurements.css * measurements.dpr * .9)));
      await info.attach('artwork-geometry', { body: JSON.stringify(measurements), contentType: 'application/json' });
      await page.locator('#albums-grid .card').nth(17).scrollIntoViewIfNeeded();
      const scroll = await page.locator('#main').evaluate(e => e.scrollTop);
      await page.evaluate(() => { window.dispatchEvent(new Event('pagehide')); window.dispatchEvent(new Event('pageshow')); });
      await page.reload(); await expect(page.locator('#page-info')).toHaveText('Page 10 / 11');
      await expect.poll(() => page.locator('#main').evaluate(e => e.scrollTop)).toBe(scroll);
      await shot(page, info, 'reload-page10', mode);
    });
  });
}
