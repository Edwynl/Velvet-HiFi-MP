import { expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
export async function prepare(page, standalone = false) {
  await page.route('https://fonts.googleapis.com/**', r => r.abort());
  await page.route('https://fonts.gstatic.com/**', r => r.abort());
  await page.addInitScript(({ standalone }) => {
    // Deterministic UI tests only: decoding/output/hardware quality are separate checks.
    const playing = new WeakMap();
    Object.defineProperty(HTMLMediaElement.prototype, 'paused', { get() { return !playing.get(this); } });
    HTMLMediaElement.prototype.play = function() { playing.set(this, true); this.dispatchEvent(new Event('playing')); return Promise.resolve(); };
    HTMLMediaElement.prototype.pause = function() { playing.set(this, false); this.dispatchEvent(new Event('pause')); };
    HTMLMediaElement.prototype.load = function() {};
    if (standalone) {
      Object.defineProperty(navigator, 'standalone', { get: () => true });
      const original = window.matchMedia.bind(window);
      window.matchMedia = q => q === '(display-mode: standalone)' ? { ...original(q), matches: true, addEventListener() {}, removeEventListener() {} } : original(q);
    }
  }, { standalone });
}
export async function go(page, view, params = {}) {
  await page.evaluate(async ({ view, params }) => { await navigate(view, params); }, { view, params });
  await expect(page.locator('#view-container')).toHaveClass('view-' + view);
}
export async function albumsPage(page, n = 4) {
  await page.goto('/#albums');
  await expect(page.locator('#page-info')).toHaveText('Page 1 / 11');
  for (let i = 2; i <= n; i++) {
    await page.getByRole('button', { name: 'Next →', exact: true }).click();
    await expect(page.locator('#page-info')).toHaveText(`Page ${i} / 11`);
  }
}
export async function shot(page, info, name, mode = 'browser') {
  const dir = `artifacts/frontend-mobile-qa/after/${info.project.name}-${mode}`;
  await mkdir(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png`, animations: 'disabled', scale: 'css' });
}
export async function noOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  const main = await page.locator('#main').boundingBox();
  expect(main.width).toBeGreaterThan(100);
  expect(main.height).toBeGreaterThan(100);
}
