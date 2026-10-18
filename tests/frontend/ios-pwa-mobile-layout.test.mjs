import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Static regression checks; only iOS Safari on a real phone can establish
// actual viewport/safe-area measurements and touch hit testing.
const html = readFileSync('static/index.html', 'utf8');
const marker = '/* iPhone portrait viewport + menu alignment:';
const start = html.indexOf(marker);
const end = html.indexOf('</style>', start);
assert.ok(start >= 0 && end > start, 'mobile compatibility stylesheet exists');
const rules = html.slice(start, end);

assert.match(rules, /@media\s*\(max-width:\s*768px\)\s*and\s*\(orientation:\s*portrait\)/,
  'the adjustments stay scoped to phone portrait, not tablet/landscape/desktop');
assert.match(rules, /#app\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)\s+auto;/s,
  'root grid cannot widen beyond the phone viewport');
assert.match(rules, /#main\s*\{[^}]*min-width:\s*0;[^}]*overflow-x:\s*hidden;[^}]*padding-top:\s*env\(safe-area-inset-top,\s*0px\);/s,
  'scrollable content clips horizontal overflow and respects the status bar');
assert.match(rules, /#view-container\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*max-width:\s*100%;/s,
  'view content does not force a wide min-content grid');
assert.match(rules, /\.grid,\s*\.artist-grid\s*\{\s*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\);/s,
  'album and artist grids allow their four tracks to shrink');

const menu = rules.match(/\.mobile-menu-btn\s*\{([^}]*)\}/)?.[1];
assert.ok(menu, 'mobile menu has a phone-specific rule');
assert.match(menu, /position:\s*fixed;/);
assert.match(menu, /top:\s*calc\(env\(safe-area-inset-top,\s*0px\)\s*\+\s*10px\);/);
assert.match(menu, /left:\s*12px;/);
assert.match(menu, /width:\s*44px;/);
assert.match(menu, /height:\s*44px;/);
assert.match(menu, /z-index:\s*46;/);
assert.match(rules, /\.topbar\s*\{[^}]*top:\s*0;[^}]*padding-left:\s*68px;/s,
  'sticky search header is not shifted by the safe area twice');
assert.match(rules, /#sidebar\s*\{[^}]*padding-top:\s*env\(safe-area-inset-top,\s*0px\);/s);

assert.match(html, /<button class="mobile-menu-btn" id="mobile-menu-btn">/);
assert.match(html, /menuBtn\.addEventListener\('click',\s*openMenu\)/);
assert.match(html, /\.sidebar-overlay\s*\{[^}]*z-index:\s*45;/s,
  'menu hit target is above the closed-overlay stacking level');
assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1\.0, viewport-fit=cover">/);
assert.doesNotMatch(html, /user-scalable\s*=\s*no/i,
  'do not hide layout regressions by disabling pinch zoom');
assert.match(html, /@media\s*\(display-mode:\s*standalone\)/,
  'the existing standalone bottom-height fix is preserved');
assert.ok([...html.matchAll(/#player\s*\{([^}]*)\}/gs)].some(([, rule]) =>
  rule.includes('env(safe-area-inset-bottom)')),
'mini player still preserves Home Indicator safe area');

console.log('ios_pwa_mobile_layout_contract=OK (iPhone touch/viewport validation pending)');
