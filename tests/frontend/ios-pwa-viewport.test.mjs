import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Static contract test: actual WebKit viewport behavior requires an iPhone.
const html = readFileSync('static/index.html', 'utf8');
const marker = '/* iOS Home Screen PWA: some WebKit versions';
const start = html.indexOf(marker);
assert.ok(start >= 0, 'iOS Home Screen viewport fix exists');
const end = html.indexOf('</style>', start);
assert.ok(end > start, 'fix is inside the document stylesheet');
const fix = html.slice(start, end);

assert.match(fix, /@media\s*\(display-mode:\s*standalone\)/,
  'Only installed Web Apps should receive the viewport override');
assert.match(fix, /@supports\s*\(-webkit-touch-callout:\s*none\)/,
  'Only WebKit-style iOS rendering should receive the override');
assert.match(fix, /#app\s*\{[^}]*height:\s*100vh;[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto;/s);
assert.match(fix, /#main\s*\{[^}]*min-height:\s*0;/s);
assert.match(fix, /#sidebar,\s*\.mobile-player-overlay\s*\{[^}]*height:\s*100vh;/s);

const normalStyles = html.slice(0, start);
assert.match(normalStyles, /#app\s*\{[^}]*height:\s*100vh;\s*height:\s*100dvh;/s,
  'Browser layout retains dynamic viewport behavior');
assert.match(normalStyles, /#player\s*\{[^}]*env\(safe-area-inset-bottom\)/s,
  'Mobile player continues to reserve Home Indicator safe area');
assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1\.0, viewport-fit=cover">/);
console.log('ios_pwa_standalone_viewport_contract=OK');
