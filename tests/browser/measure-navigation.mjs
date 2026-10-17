// Comparable before/after evidence with a deliberate 400ms API response delay.
// This models latency; it is not a real Tailscale bandwidth measurement.
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import path from 'node:path';
import { createFixtureServer } from './fixture-server.mjs';
import { prepare } from './helpers.mjs';
const output = path.resolve('artifacts/frontend-mobile-qa/comparison'); await mkdir(output, { recursive: true });
const results = [];
for (const [name, root] of [['before', path.resolve('.temp/baseline')], ['after', process.cwd()]]) {
  const server = createFixtureServer({ root }); await new Promise(r => server.listen(0, '127.0.0.1', r));
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 430, height: 932 }, deviceScaleFactor: 3,
    hasTouch: true, serviceWorkers: 'block', recordVideo: { dir: output, size: { width: 430, height: 932 } } });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  const page = await context.newPage(); const requests = [], sizes = [], pending = [];
  await prepare(page);
  await page.route('**/api/**', async route => { await new Promise(r => setTimeout(r, 400)); await route.continue(); });
  page.on('response', r => {
    requests.push({ method: r.request().method(), path: new URL(r.url()).pathname, status: r.status() });
    if (r.url().includes('/api/covers/')) pending.push(r.body().then(b => sizes.push(b.length)).catch(() => {}));
  });
  try {
    const start = performance.now();
    await page.goto(`http://127.0.0.1:${server.address().port}/#albums`);
    await page.locator('#albums-grid .card').first().waitFor();
    const firstCatalogMs = performance.now() - start;
    for (let i = 2; i <= 4; i++) { await page.locator('#next-page-btn').click(); await page.locator('#page-info').filter({ hasText: `Page ${i} /` }).waitFor(); }
    const card = page.locator('#albums-grid .card').nth(17); await card.scrollIntoViewIfNeeded();
    const scrollBefore = await page.locator('#main').evaluate(e => e.scrollTop);
    await page.screenshot({ path: path.join(output, `${name}-albums-page4.png`), scale: 'css' });
    await card.click(); await page.locator('#play-all-btn').click();
    for (let i = 2; i <= 5; i++) {
      await page.locator('#mobile-btn-next').click();
      await page.locator('#player-title').filter({ hasText: `Movement ${i}` }).waitFor();
    }
    await page.evaluate(async () => { await api('/stream-ticket', { method: 'POST' }); await api('/history', { method: 'POST', body: '{}' }); });
    await page.locator('#mobile-btn-play').click(); await page.locator('#mobile-btn-play').click();
    await page.screenshot({ path: path.join(output, `${name}-track5.png`), scale: 'css' });
    await page.locator('#mobile-player-back').click();
    const backStart = performance.now(); await page.locator('.back-btn').click(); await page.locator('#page-info').waitFor();
    const backMs = performance.now() - backStart;
    const pageInfo = await page.locator('#page-info').textContent();
    const scrollAfter = await page.locator('#main').evaluate(e => e.scrollTop);
    await page.screenshot({ path: path.join(output, `${name}-back.png`), scale: 'css' });
    await Promise.all(pending);
    const assets = {};
    for (const file of ['static/index.html', 'static/js/library-views.js', 'static/js/library-cards.js', 'static/js/player-ui.js', 'static/js/dsp-controls.js']) {
      const data = await readFile(path.join(root, file)); assets[file] = { bytes: data.length, gzipBytes: gzipSync(data).length };
    }
    results.push({ name, apiDelayMs: 400, firstCatalogMs, backMs, pageInfo, scrollBefore, scrollAfter,
      coverResponses: sizes.length, syntheticCoverBytes: sizes.reduce((a, b) => a + b, 0), assets, requests });
    await context.tracing.stop({ path: path.join(output, `${name}-back-trace.zip`) });
    const video = page.video(); await context.close(); await video.saveAs(path.join(output, `${name}-back.webm`));
  } finally { await browser.close(); await new Promise(r => server.close(r)); }
}
await writeFile(path.join(output, 'metrics.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results.map(({ requests, ...rest }) => rest), null, 2));
