import { test, expect } from '@playwright/test';
import { prepare } from './helpers.mjs';
test.use({ serviceWorkers: 'allow' });

test('real service worker installs one release; offline shell shows a retry instead of a blank page', async ({ page, context }) => {
  await prepare(page);
  await page.goto('/#albums'); await expect(page.locator('#albums-grid .card').first()).toBeVisible();
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  const files = await page.evaluate(async () => {
    const cache = await caches.open('velvet-v8'); return (await cache.keys()).map(r => new URL(r.url).pathname + new URL(r.url).search);
  });
  expect(files).toHaveLength(10);
  expect(files).toContain('/static/js/settings-view.js?v=mobile-20261017-1');
  expect(files).toContain('/static/mobile-layout.css?v=mobile-20261017-1');
  await page.reload(); await expect(page.locator('#albums-grid .card').first()).toBeVisible();
  await context.setOffline(true);
  await page.reload(); await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
  await context.setOffline(false);
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await expect(page.locator('#albums-grid .card').first()).toBeVisible();
});

test('an available update does not activate during playback; paused user action activates it', async ({ page }) => {
  await prepare(page);
  await page.addInitScript(() => {
    window.__updateMessages = [];
    Object.defineProperty(navigator, 'serviceWorker', { value: {
      async register() { return { waiting: { postMessage(m) { window.__updateMessages.push(m); } }, addEventListener() {} }; },
      addEventListener() {},
    } });
  });
  await page.goto('/#album/1'); await page.locator('#play-all-btn').click();
  await expect.poll(() => page.evaluate(() => state.playing)).toBe(true);
  await page.evaluate(() => toggleMobilePlayer(false));
  await page.locator('#pwa-update-btn').click();
  expect(await page.evaluate(() => window.__updateMessages)).toEqual([]);
  await expect(page.locator('#player-title')).toHaveText('Movement 1');
  await page.locator('#btn-play').click();
  await expect.poll(() => page.evaluate(() => state.playing)).toBe(false);
  await page.locator('#pwa-update-btn').click();
  expect(await page.evaluate(() => window.__updateMessages)).toEqual([{ type: 'ACTIVATE_UPDATE' }]);
});
