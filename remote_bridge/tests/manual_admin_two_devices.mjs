// Browser acceptance with Velvet's administrator password set, and two
// devices (separate browser sessions, separate cookies) on one bridge session.
// Not run by the unit tests. Start the stack first, from the repository root:
//
//   set ADMIN_PASSWORD=velvet-admin-test & set TRACK_SECONDS=25
//   venv\Scripts\python.exe remote_bridge/tests/manual_browser_stack.py <repo root> <venv python> <ffmpeg>
//
// then, once it prints READY:
//
//   set ADMIN_PASSWORD=velvet-admin-test
//   node remote_bridge/tests/manual_admin_two_devices.mjs [bridge URL] [renderer log URL]
//
// Needs Playwright (local or global npm install) with Chromium. HEADED=1 shows the windows.
import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
let playwright;
try {
  playwright = require('playwright');
} catch {
  playwright = require(path.join(execSync('npm root -g').toString().trim(), 'playwright'));
}

const BASE = process.argv[2] || 'http://127.0.0.1:18877';
const LOG = process.argv[3] || 'http://127.0.0.2:18999/log';
const PASSWORD = process.env.ADMIN_PASSWORD;
const UDN = 'uuid:fake-exn100';
if (!PASSWORD) throw new Error('Set ADMIN_PASSWORD to the stack\'s administrator password');

const results = [];
function check(name, ok, detail = '') {
  results.push([name, Boolean(ok)]);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${!ok && detail ? `  [${typeof detail === 'string' ? detail : JSON.stringify(detail)}]` : ''}`);
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(probe, ms = 8000) {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await probe();
    if (value || Date.now() > deadline) return value;
    await sleep(250);
  }
}
const session = async () => (await fetch(`${BASE}/api/bridge/session/${UDN}`)).json();
const loads = async () => (await (await fetch(LOG)).json()).filter(e => e.event === 'load').map(e => e.title);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// What a page shows: its queue (track ids), current track, repeat mode, zone.
const view = page => page.evaluate(() => ({
  ids: state.queue.map(t => t.id), current: state.currentTrack && state.currentTrack.id,
  repeat: state.repeatMode, udn: state.castDevice && state.castDevice.udn,
  repeatColor: document.getElementById('btn-repeat').style.color,
}));

async function device(browser, name) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  const queuePosts = [];
  page.on('response', response => {
    if (response.url().includes('/bridge/queue/')) queuePosts.push(response.status());
  });
  page.on('pageerror', error => console.log(`[${name}] page error: ${error.message}`));
  return { context, page, queuePosts, name };
}

// Velvet's own administrator dialog, if it opens within `ms` (it asks once
// per browser session, at the first protected request).
async function unlockIfAsked(page, ms = 3000) {
  const asked = await page.waitForSelector('#admin-verification-password', { timeout: ms }).catch(() => null);
  if (asked) await unlock(page);
  return Boolean(asked);
}

async function unlock(page) {
  await page.waitForSelector('#admin-verification-password', { timeout: 10000 });
  await page.fill('#admin-verification-password', PASSWORD);
  await page.click('#admin-verification-submit');
  await page.waitForSelector('#admin-verification', { state: 'detached', timeout: 10000 });
}

const browser = await playwright.chromium.launch({ headless: !process.env.HEADED });
try {
  const phone = await device(browser, 'phone');
  const tablet = await device(browser, 'tablet');
  const security = await (await fetch(`${BASE}/api/security/status`)).json();
  check('parent requires the administrator password', security.admin_protected && !security.admin_unlocked, security);

  // ── Phone: pick the EXN100, Play All, unlock in Velvet's own dialog ──
  await phone.page.goto(`${BASE}/#album/1`);
  await phone.page.waitForSelector('#play-all-btn');
  await phone.page.click('#btn-cast');
  await phone.page.click('.cast-item:has-text("EXN100")');
  let asked = await unlockIfAsked(phone.page);
  await phone.page.click('#play-all-btn');
  asked = (await unlockIfAsked(phone.page)) || asked;
  check('phone: Velvet asked for the administrator password', asked);
  let s = await until(async () => { const x = await session(); return x.active && x.track_id === 1 && x; });
  check('phone: cast plays after the admin unlock', s && s.state === 'PLAYING', s);
  const album = s.ids;

  // ── P1-1: a queue edit through the browser's own cookies ──
  await phone.page.click('#btn-repeat'); // none -> all
  s = await until(async () => { const x = await session(); return x.repeat === 'all' && x; });
  check('phone: repeat change reaches the bridge (admin cookie on /api/bridge/queue)', s, await session());
  check('phone: no queue edit refused for admin access', !phone.queuePosts.includes(403), phone.queuePosts);

  // ── P1-2: a cold page on the tablet keeps Repeat All ──
  await tablet.page.goto(`${BASE}/`);
  let t = await until(async () => { const v = await view(tablet.page); return v.udn === UDN && v.current === 1 && v; });
  check('tablet: cold page reattaches to the EXN100 session', t, await view(tablet.page));
  check('tablet: shows the session\'s Repeat All', t && t.repeat === 'all' && t.repeatColor.includes('gold'), t);
  const revision = (await session()).revision;
  await sleep(4500); // two sync cycles
  s = await session();
  check('tablet: reattaching changed nothing on the bridge', s.repeat === 'all' && s.revision === revision, s);
  check('tablet: sent no queue edit', tablet.queuePosts.length === 0, tablet.queuePosts);

  // ── P1-3: phone edits upcoming tracks, the tablet takes it in ──
  await phone.page.evaluate(() => { state.queue.splice(state.queue.findIndex(x => x.id === 3), 1); });
  const withoutThree = album.filter(id => id !== 3);
  s = await until(async () => { const x = await session(); return same(x.ids, withoutThree) && x; });
  check('phone: removal of an upcoming track reaches the bridge', s, await session());
  t = await until(async () => { const v = await view(tablet.page); return same(v.ids, withoutThree) && v; });
  check('tablet: takes in the removal while the same song plays on', t && t.current === 1, await view(tablet.page));

  // The tablet's own edit needs its own admin unlock (another browser session).
  await tablet.page.click('#btn-repeat'); // all -> one
  check('tablet: its first edit opens Velvet\'s admin dialog (not a silent failure)', await unlockIfAsked(tablet.page, 6000));
  s = await until(async () => { const x = await session(); return x.repeat === 'one' && x; });
  check('tablet: repeat toggle (after its unlock) reaches the bridge', s, await session());
  check('tablet: its toggle did not bring the removed track back', s && same(s.ids, withoutThree), s);

  // ── Both edit at once: phone shuffles, tablet toggles repeat ──
  await Promise.all([phone.page.click('#btn-shuffle'), tablet.page.click('#btn-repeat')]); // one -> none
  const shuffled = (await view(phone.page)).ids;
  const converged = await until(async () => {
    const [x, p, q] = [await session(), await view(phone.page), await view(tablet.page)];
    return x.repeat === 'none' && same(x.ids, shuffled) && same(p.ids, x.ids) && same(q.ids, x.ids)
      && p.repeat === 'none' && q.repeat === 'none' && x;
  }, 10000);
  check('simultaneous shuffle (phone) and repeat (tablet): both kept, both pages agree', converged,
    { session: await session(), phone: await view(phone.page), tablet: await view(tablet.page) });
  check('no device was refused for admin access after unlocking',
    !phone.queuePosts.includes(403) && tablet.queuePosts.filter(code => code === 403).length <= 1,
    { phone: phone.queuePosts, tablet: tablet.queuePosts });

  // ── P1-4: the bridge's own advance follows the edited queue ──
  s = await session();
  const next = s.ids[s.queue_index + 1];
  const advanced = await until(async () => { const x = await session(); return x.track_id !== 1 && x; }, 40000);
  check('auto-next plays the next track of the edited queue', advanced && advanced.track_id === next,
    { expected: next, session: advanced });
  check('the removed track was never loaded', !(await loads()).includes('Song 3'), await loads());
  const follows = await until(async () => {
    const [p, q] = [await view(phone.page), await view(tablet.page)];
    return p.current === advanced.track_id && q.current === advanced.track_id;
  });
  check('both pages follow the advance', follows, { phone: await view(phone.page), tablet: await view(tablet.page) });

  // ── Reviewer's P1-2 regression: Repeat All, cold reload, wraps after the last song ──
  await phone.page.click('#btn-repeat'); // none -> all
  s = await until(async () => { const x = await session(); return x.repeat === 'all' && x; });
  check('phone: Repeat All set again', s, await session());
  await phone.page.reload();
  const p = await until(async () => { const v = await view(phone.page); return v.udn === UDN && v; });
  await sleep(4500);
  s = await session();
  check('phone after a cold reload: Repeat All on screen and on the bridge', p && p.repeat === 'all' && s.repeat === 'all',
    { phone: p, session: s });
  // Jump to the last song from the queue panel, then let it end.
  await phone.page.click('#btn-queue-toggle');
  await phone.page.locator('.queue-item').last().click();
  const last = s.ids[s.ids.length - 1];
  s = await until(async () => { const x = await session(); return x.track_id === last && x; }, 15000);
  check('phone: jumped to the last song', s, await session());
  const wrapped = await until(async () => { const x = await session(); return x.track_id === s.ids[0] && x; }, 40000);
  check('Repeat All wraps to the first song after the last', wrapped, await session());
} finally {
  await browser.close();
}
const passed = results.filter(([, ok]) => ok).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
