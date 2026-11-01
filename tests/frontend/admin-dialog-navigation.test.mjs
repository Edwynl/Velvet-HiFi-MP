import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

// 管理员验证弹窗是覆盖整个窗口的模态框。提出它的视图离开时，弹窗必须跟着
// 一起离开：它的遮罩会盖住整个应用，吞掉所有指向下层视图的点击。

let adminLogins = 0;
const requests = [];
const response = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

const dom = new JSDOM(inlineFrontendModules(readFileSync('static/index.html', 'utf8')).replace(/<link[^>]+>/g, ''), {
  url: 'http://localhost/#home', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = async (url, options = {}) => {
      const path = new URL(url, 'http://localhost').pathname;
      requests.push(path);
      if (path === '/api/security/status') return response({ enabled:true, admin_protected:true,
        admin_unlocked:false, passwordless_listening:true });
      if (path === '/api/security/admin-login') {
        adminLogins++;
        return response({code:'admin_password_invalid',error:'Incorrect administrator password.'},403);
      }
      if (path === '/api/dsp/profiles' || path === '/api/recently-added' || path === '/api/playlists') return response([]);
      if (path === '/api/stats') return response({albums:1,artists:1,tracks:1});
      return response({});
    };
    window.scrollTo = () => {};
    window.matchMedia = media => ({media,matches:false,addEventListener(){},removeEventListener(){}});
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
    window.HTMLMediaElement.prototype.load = () => {};
    window.navigator.mediaSession = {setActionHandler(){}};
  },
});
const {window} = dom;
const document = window.document;
const wait = async (predicate, label) => {
  const where = new Error().stack.split('\n')[2].trim().replace(/^at\s+/, '');
  for (let i=0;i<300;i++) { if (predicate()) return; await new Promise(resolve=>setTimeout(resolve,10)); }
  throw new Error(`Timed out waiting for ${label} (${where})`);
};
const settle = () => new Promise(resolve=>setTimeout(resolve,300));
const prompt = () => document.querySelector('#admin-verification');
const goBack = () => window.dispatchEvent(new window.PopStateEvent('popstate', {
  state: { view: 'home', params: {} },
}));

await wait(()=>document.querySelector('#home-greeting'), 'the home greeting');
await settle();

// ── 从 Settings 浏览器后退 ──────────────────────────────────────────────────
const settingsNav = window.navigate('settings');
await wait(()=>prompt(), 'the administrator prompt');
assert.ok(!requests.includes('/api/config'), 'Settings stays gated while the prompt is open');

goBack();
await wait(()=>!prompt(), 'the prompt to be dismissed');
await settingsNav;
await settle();
assert.equal(prompt(), null, 'Leaving Settings takes the administrator prompt down with it');
assert.equal(window.eval('state.view'), 'home');
assert.ok(
  !document.querySelector('#view-container').textContent.includes('Could not load page'),
  'The abandoned Settings render must not paint an error over the view the user moved to',
);
assert.equal(adminLogins, 0, 'A dismissed prompt never sends the administrator password');

// ── 焦点离开弹窗后，Escape 仍然能关闭它 ──────────────────────────────────────
const escapeNav = window.navigate('settings');
await wait(()=>prompt(), 'the administrator prompt');
document.body.dispatchEvent(new window.KeyboardEvent('keydown', {
  key: 'Escape', bubbles: true, cancelable: true,
}));
await wait(()=>!prompt(), 'the prompt to be dismissed');
await escapeNav;
await settle();
assert.equal(prompt(), null, 'Escape closes the prompt even when the focus is outside it');
assert.equal(window.eval('state.view'), 'home', 'Cancelling still returns to the library');

// ── 重新渲染同一个视图时弹窗必须保留 ─────────────────────────────────────────
const againNav = window.navigate('settings');
await wait(()=>prompt(), 'the administrator prompt');
window.navigate('settings');
await settle();
assert.ok(prompt(), 'Re-rendering the same view keeps the prompt open until the user answers it');

// ── 再次离开该视图仍然会关闭弹窗，且应用之后还能再次弹出 ──────────────────────
goBack();
await wait(()=>!prompt(), 'the prompt to be dismissed');
await againNav;
await settle();
assert.equal(prompt(), null, 'The prompt is dismissed on every route change');

const lastNav = window.navigate('settings');
await wait(()=>prompt(), 'the administrator prompt');
document.querySelector('#admin-verification-cancel').click();
await wait(()=>!prompt(), 'the prompt to be dismissed');
await lastNav;
await settle();
assert.equal(window.eval('state.view'), 'home', 'Cancelling still returns to the library');
assert.equal(adminLogins, 0);

dom.window.close();
console.log('frontend_admin_dialog_navigation=OK');
