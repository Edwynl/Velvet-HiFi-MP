import { defineConfig } from '@playwright/test';
const qaPort = Number(process.env.VELVET_QA_PORT || 8767);
const baseURL = `http://127.0.0.1:${qaPort}`;
const devices = [
  ['iphone-portrait', 430, 932, 3], ['iphone-landscape', 932, 430, 3],
  ['oppo-portrait', 800, 1131, 2], ['oppo-landscape', 1131, 800, 2],
];
export default defineConfig({
  testDir: './tests/browser', testMatch: '**/*.spec.mjs', timeout: 40000,
  expect: { timeout: 8000 }, workers: 2, fullyParallel: true, retries: 0,
  outputDir: process.env.VELVET_QA_OUTPUT || 'artifacts/frontend-mobile-qa/results',
  reporter: [['list'], ['json', { outputFile: process.env.VELVET_QA_REPORT || 'artifacts/frontend-mobile-qa/browser-results.json' }]],
  use: { baseURL, trace: 'retain-on-failure', screenshot: 'only-on-failure', video: 'retain-on-failure', serviceWorkers: 'block' },
  webServer: { command: 'node tests/browser/fixture-server.mjs', url: baseURL, reuseExistingServer: false, timeout: 15000 },
  projects: ['chromium', 'webkit'].flatMap(browserName => [
    ...devices.map(([name, width, height, dpr]) => ({ name: `${browserName}-${name}`, timeout: browserName === 'webkit' ? 90000 : 40000, testMatch: 'mobile.spec.mjs',
      use: { browserName, viewport: { width, height }, deviceScaleFactor: dpr, hasTouch: true,
        userAgent: name.startsWith('iphone') ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1' : undefined } })),
    { name: `${browserName}-desktop`, testMatch: ['navigation.spec.mjs', 'pwa.spec.mjs'], use: { browserName, viewport: { width: 1440, height: 900 } } },
  ]),
});
