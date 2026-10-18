import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { inlineFrontendModules } from './inline-modules.mjs';

let unlocked = false, writes = 0, adminLogins = 0;
const requests = [];
const response = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
const dom = new JSDOM(inlineFrontendModules(readFileSync('static/index.html', 'utf8')).replace(/<link[^>]+>/g, ''), {
  url: 'http://localhost/#home', runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(window) {
    window.localStorage.setItem('velvet_api_key', 'stale-listening-key');
    window.fetch = async (url, options = {}) => {
      const path = new URL(url, 'http://localhost').pathname;
      requests.push(path);
      if (path === '/api/security/status') return response({ enabled:true, admin_protected:true,
        admin_unlocked:unlocked, passwordless_listening:true });
      if (path === '/api/security/admin-login') {
        adminLogins++;
        unlocked = JSON.parse(options.body).password === 'test-admin-password';
        return unlocked ? response({success:true}) : response({code:'admin_password_invalid',error:'Incorrect administrator password.'},403);
      }
      if (path === '/api/albums/11/set-cover' || path === '/api/albums/11/cover') {
        if (!unlocked) return response({code:'admin_password_required'},403);
        writes++; return response({success:true});
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
const wait = async predicate => {
  for (let i=0;i<200;i++) { if (predicate()) return; await new Promise(resolve=>setTimeout(resolve,10)); }
  throw new Error('Timed out waiting for admin session');
};
await wait(()=>document.querySelector('#home-greeting'));
assert.equal(document.querySelector('#login-modal'), null, 'Tailnet browsing never asks for the old access password');
assert.ok(!requests.includes('/api/security/login'), 'Stale saved keys do not trigger the old login flow');
const settings = window.navigate('settings');
await wait(()=>document.querySelector('#admin-verification'));
assert.ok(!requests.includes('/api/config'), 'Settings is gated before loading private configuration');
document.querySelector('#admin-verification-cancel').click();
await settings;
assert.equal(window.eval('state.view'), 'home');
const cast = window.activateZone({type:'cast',id:'renderer',udn:'renderer'},false);
await wait(()=>document.querySelector('#admin-verification'));
document.querySelector('#admin-verification-cancel').click();
await cast;
assert.equal(window.eval('state.outputMode'), 'browser', 'Cancelled admin unlock never switches the output');
assert.ok(!requests.some(path=>path.startsWith('/api/cast/')));
const changes = Promise.all([
  window.api('/albums/11/set-cover',{method:'POST',body:'{}'}),
  window.api('/albums/11/set-cover',{method:'POST',body:'{}'}),
]);
await wait(()=>document.querySelector('#admin-verification-password'));
assert.equal(document.querySelectorAll('#admin-verification').length, 1, 'Concurrent requests share one unlock');
const submit = password => {
  document.querySelector('#admin-verification-password').value=password;
  document.querySelector('#admin-verification form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
};
submit('wrong');
await wait(()=>document.querySelector('#admin-verification-error').textContent.includes('Incorrect'));
assert.equal(writes, 0);
submit('test-admin-password');
await changes;
assert.equal(writes, 2, 'Original operations retry only after a successful unlock');
assert.equal(document.querySelector('#admin-verification'), null);
assert.equal(adminLogins, 2, 'Only the wrong attempt and one successful unlock sent a password');
assert.equal(await window.ensureAdminSession(), true);
await window.managementFetch('/api/albums/11/cover',{method:'POST',body:new window.FormData()});
assert.equal(writes, 3, 'Multipart cover uploads share the same admin session');
assert.equal(adminLogins, 2, 'Later management does not prompt again');
assert.ok(!JSON.stringify({...window.localStorage}).includes('test-admin-password'));
dom.window.close();
console.log('frontend_admin_session=OK');
