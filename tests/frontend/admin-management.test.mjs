import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const source = readFileSync('static/index.html', 'utf8');
const helpers = source.slice(source.indexOf('let adminDialogActive ='),
  source.indexOf('// Views kept alive across back/forward:'));
const ADMIN = 'test-admin-only';
const waitFor = async predicate => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const result = predicate();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('Timed out waiting for administrator dialog');
};

function harness({ setup = false } = {}) {
  const dom = new JSDOM('<button id="original">Manage</button>', {
    url: 'http://localhost:8765', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  let writes = 0;
  const calls = [];
  const navigation = [];
  dom.window.authState = { apiKey: 'shared-listening-key' };
  dom.window.navigate = view => navigation.push(view);
  dom.window.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const json = (body, status) => new Response(JSON.stringify(body), { status });
    if ((options.method || 'GET') === 'GET' || url.endsWith('/radio')) return json({ tracks: [] }, 200);
    if (setup) return json({ code: 'admin_setup_required', error: 'Set an administrator password first.' }, 403);
    if (!options.headers?.['X-Admin-Password']) return json({ code: 'admin_password_required' }, 403);
    if (options.headers['X-Admin-Password'] !== ADMIN) {
      return json({ code: 'admin_password_invalid', error: 'Incorrect administrator password.' }, 403);
    }
    writes += 1;
    return json({ success: true }, 200);
  };
  dom.window.eval(helpers);
  const start = (path = '/api/albums/1/open-folder') => dom.window.managementFetch(path, { method: 'POST' });
  const field = () => dom.window.document.querySelector('#admin-verification-password');
  const submit = password => {
    field().value = password;
    dom.window.document.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  };
  return { dom, start, field, submit, calls, navigation, get writes() { return writes; } };
}

// Shared credentials never change data; cancelling a challenge makes no write.
{
  const h = harness();
  try {
    h.dom.window.document.querySelector('#original').focus();
    const pending = h.start();
    await waitFor(h.field);
    assert.equal(h.writes, 0);
    assert.equal(h.calls[0].options.headers['X-API-Key'], 'shared-listening-key');
    h.dom.window.document.querySelector('#admin-verification-cancel').click();
    assert.equal((await pending).status, 403);
    assert.equal(h.writes, 0);
    assert.equal(h.dom.window.document.activeElement.id, 'original');
  } finally { h.dom.window.close(); }
}

// Wrong passwords leave the dialog open. Correct approval performs ONE write;
// a later operation asks again, and no password is saved in browser storage.
{
  const h = harness();
  try {
    const pending = h.start();
    await waitFor(h.field);
    h.submit('shared-listening-password');
    await waitFor(() => h.dom.window.document.querySelector('[role="alert"]').textContent.includes('Incorrect'));
    assert.equal(h.writes, 0);
    assert.equal(h.field().value, '');
    h.submit(ADMIN);
    assert.equal((await pending).status, 200);
    assert.equal(h.writes, 1);
    assert.equal(h.dom.window.document.querySelector('#admin-verification'), null);
    assert.equal(h.dom.window.localStorage.length, 0);
    assert.equal(h.dom.window.sessionStorage.length, 0);
    const next = h.start('/api/albums/2/open-folder');
    await waitFor(h.field);
    assert.equal(h.writes, 1);
    h.field().dispatchEvent(new h.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal((await next).status, 403);
    assert.equal(h.writes, 1);
  } finally { h.dom.window.close(); }
}

// First setup is clearly separated from approval and cannot submit a mutation.
{
  const h = harness({ setup: true });
  try {
    const pending = h.start();
    await waitFor(() => h.dom.window.document.querySelector('#admin-verification'));
    assert.equal(h.field(), null);
    assert.match(h.dom.window.document.body.textContent, /server PC first/);
    h.dom.window.document.querySelector('form').dispatchEvent(new h.dom.window.Event('submit', { bubbles: true, cancelable: true }));
    assert.equal((await pending).status, 403);
    assert.deepEqual(h.navigation, ['settings']);
    assert.equal(h.writes, 0);
  } finally { h.dom.window.close(); }
}

// Browsing and radio do not ask for administrator verification.
{
  const h = harness();
  try {
    await h.dom.window.managementFetch('/api/albums/1');
    await h.dom.window.managementFetch('/api/albums/1/radio');
    assert.equal(h.dom.window.document.querySelector('#admin-verification'), null);
    assert.equal(h.writes, 0);
  } finally { h.dom.window.close(); }
}

console.log('frontend_admin_management=OK');
