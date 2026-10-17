import { chromium, webkit } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { createFixtureServer } from './fixture-server.mjs';
const out = 'artifacts/frontend-mobile-qa/before';
await mkdir(out, { recursive: true });
const server = createFixtureServer(); await new Promise(r => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;
const results = [];
for (const [engine, browserType] of Object.entries({ chromium, webkit })) {
  const browser = await browserType.launch();
  for (const [device, viewport, dpr] of [
    ['iphone-portrait', { width: 430, height: 932 }, 3], ['iphone-landscape', { width: 932, height: 430 }, 3],
    ['oppo-portrait', { width: 800, height: 1131 }, 2], ['oppo-landscape', { width: 1131, height: 800 }, 2],
  ]) {
    const context = await browser.newContext({ viewport, deviceScaleFactor: dpr, hasTouch: true, serviceWorkers: 'block' });
    const page = await context.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('https://fonts.googleapis.com/**', route => route.abort());
    await page.goto(base + '/#albums');
    await page.locator('#next-page-btn').waitFor();
    for (let i = 0; i < 3; i++) { await page.locator('#next-page-btn').click(); await page.locator('#page-info').filter({ hasText: `Page ${i + 2} /` }).waitFor(); }
    await page.locator('#albums-grid .card').nth(17).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${out}/${engine}-${device}-albums.png` });
    const original = await page.locator('#main').evaluate(e => e.scrollTop);
    const cover = await page.locator('#albums-grid .card img').first().evaluate(e => ({ width: e.getBoundingClientRect().width, natural: e.naturalWidth, src: e.currentSrc }));
    await page.locator('#albums-grid .card').nth(17).click(); await page.locator('#play-all-btn').waitFor();
    await page.screenshot({ path: `${out}/${engine}-${device}-album.png` });
    await page.evaluate(async () => { await api('/stream-ticket', { method: 'POST' }); await api('/history', { method: 'POST', body: '{}' }); });
    let backClickError = '';
    try { await page.locator('.back-btn').click({ timeout: 3000 }); }
    catch (e) { backClickError = e.message.split('\n')[0]; await page.evaluate(() => handleHistoryBack('albums')); }
    await page.locator('#page-info').waitFor();
    results.push({ engine, device, originalScroll: original, returnedScroll: await page.locator('#main').evaluate(e => e.scrollTop), page: await page.locator('#page-info').textContent(), cover, errors, backClickError });
    await page.screenshot({ path: `${out}/${engine}-${device}-back.png` });
    await context.close();
  }
  await browser.close();
}
await writeFile(`${out}/baseline.json`, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
await new Promise(r => server.close(r));

