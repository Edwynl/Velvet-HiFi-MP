import { readFileSync } from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

import { JSDOM } from 'jsdom';

const ROOT = process.cwd();
const HTML_PATH = path.join(ROOT, 'static', 'index.html');

function makeJsonResponse(body, status = 200) {
  const payload = structuredClone(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        return String(name).toLowerCase() === 'content-type' ? 'application/json' : null;
      },
    },
    async json() {
      return structuredClone(payload);
    },
    async text() {
      return JSON.stringify(payload);
    },
  };
}

function createHarness() {
  let nextProfileId = 100;
  const requests = [];
  const dspProfiles = [
    {
      id: 2,
      name: 'Black Background',
      short_name: 'Black',
      description: 'Factory preset for a darker, cleaner backdrop.',
      category: 'taste',
      preset_key: 'black_background',
      is_factory: 1,
      preamp_db: -4.0,
      filter_chain_json: JSON.stringify([
        { type: 'low_shelf', freq: 90, gain: -0.8, q: 0.7, enabled: true },
      ]),
      tags: JSON.stringify(['dark']),
      sort_order: 10,
    },
    {
      id: 3,
      name: 'Vocal Forward Warm',
      short_name: 'Vocal Warm',
      description: 'Factory preset for forward, warm vocals.',
      category: 'taste',
      preset_key: 'vocal_forward_warm',
      is_factory: 1,
      preamp_db: -3.0,
      filter_chain_json: JSON.stringify([
        { type: 'peaking', freq: 1800, gain: 1.5, q: 0.9, enabled: true },
      ]),
      tags: JSON.stringify(['vocals']),
      sort_order: 20,
    },
  ];

  function normalizeProfile(profile) {
    return {
      description: '',
      category: 'custom',
      preset_key: null,
      is_factory: 0,
      preamp_db: 0,
      filter_chain_json: '[]',
      tags: '[]',
      sort_order: 999,
      ...profile,
    };
  }

  function route(url, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const parsedUrl = new URL(url, 'http://localhost');
    const body = opts.body ? JSON.parse(opts.body) : null;
    requests.push({ method, path: parsedUrl.pathname, body });

    if (parsedUrl.pathname === '/api/stats' && method === 'GET') {
      return makeJsonResponse({ artists: 0, albums: 0, tracks: 0, duration: 0 });
    }

    if (parsedUrl.pathname === '/api/scan/status' && method === 'GET') {
      return makeJsonResponse({ running: false, progress: 0, total: 0, current_file: '' });
    }

    if (parsedUrl.pathname === '/api/config' && method === 'GET') {
      return makeJsonResponse({
        music_dir: 'C:/Music',
        libraries: [{ id: 1, path: 'C:/Music' }],
        port: 9876,
        data_dir: 'C:/VELVET_DATA',
      });
    }

    if (parsedUrl.pathname === '/api/enrich/fingerprint/status' && method === 'GET') {
      return makeJsonResponse({ running: false, progress: 0, total: 0, current_file: '' });
    }

    if (parsedUrl.pathname === '/api/enrich/biography/status' && method === 'GET') {
      return makeJsonResponse({ running: false, progress: 0, total: 0, current_file: '' });
    }

    if (parsedUrl.pathname === '/api/enrich/bios/status' && method === 'GET') {
      return makeJsonResponse({ running: false, progress: 0, total: 0, current_artist: '' });
    }

    if (parsedUrl.pathname === '/api/dsp/profiles' && method === 'GET') {
      return makeJsonResponse(
        dspProfiles
          .map(normalizeProfile)
          .sort((a, b) => (a.sort_order - b.sort_order) || String(a.name).localeCompare(String(b.name)))
      );
    }

    if (parsedUrl.pathname === '/api/dsp/profiles' && method === 'POST') {
      const profile = normalizeProfile({
        id: nextProfileId++,
        name: body?.name || 'Custom DSP',
        description: body?.description || '',
        category: body?.category || 'custom',
        preamp_db: body?.preamp_db ?? 0,
        filter_chain_json: JSON.stringify(body?.filters || body?.filter_chain_json || []),
        tags: JSON.stringify(body?.tags || ['custom']),
        sort_order: 999,
      });
      dspProfiles.push(profile);
      return makeJsonResponse({ id: profile.id, name: profile.name });
    }

    const updateMatch = parsedUrl.pathname.match(/^\/api\/dsp\/profiles\/(\d+)$/);
    if (updateMatch && method === 'GET') {
      const profile = dspProfiles.find(item => String(item.id) === updateMatch[1]);
      return profile
        ? makeJsonResponse(normalizeProfile(profile))
        : makeJsonResponse({ detail: 'Not found' }, 404);
    }

    if (updateMatch && method === 'PUT') {
      const profile = dspProfiles.find(item => String(item.id) === updateMatch[1]);
      if (!profile) return makeJsonResponse({ detail: 'Not found' }, 404);
      Object.assign(profile, {
        name: body?.name ?? profile.name,
        description: body?.description ?? profile.description,
        category: body?.category ?? profile.category,
        preamp_db: body?.preamp_db ?? profile.preamp_db,
        filter_chain_json: JSON.stringify(body?.filters || body?.filter_chain_json || []),
        tags: JSON.stringify(body?.tags || JSON.parse(profile.tags || '[]')),
      });
      return makeJsonResponse({ success: true });
    }

    if (updateMatch && method === 'DELETE') {
      const idx = dspProfiles.findIndex(item => String(item.id) === updateMatch[1]);
      if (idx >= 0) dspProfiles.splice(idx, 1);
      return makeJsonResponse({ success: true });
    }

    const cloneMatch = parsedUrl.pathname.match(/^\/api\/dsp\/profiles\/(\d+)\/clone$/);
    if (cloneMatch && method === 'POST') {
      const source = dspProfiles.find(item => String(item.id) === cloneMatch[1]);
      if (!source) return makeJsonResponse({ detail: 'Not found' }, 404);
      const clone = normalizeProfile({
        ...source,
        id: nextProfileId++,
        name: body?.name || `${source.name} Custom`,
        description: body?.description || `Custom copy of ${source.name}`,
        category: 'custom',
        preset_key: null,
        is_factory: 0,
      });
      dspProfiles.push(clone);
      return makeJsonResponse({ id: clone.id, name: clone.name });
    }

    if (parsedUrl.pathname === '/api/recently-added' && method === 'GET') {
      return makeJsonResponse([]);
    }

    throw new Error(`Unhandled fetch: ${method} ${parsedUrl.pathname}`);
  }

  return {
    requests,
    dspProfiles,
    async createDom() {
      const html = readFileSync(HTML_PATH, 'utf8')
        .replace(/<link[^>]+>/g, '');
      const dom = new JSDOM(html, {
        url: 'http://localhost/#settings',
        runScripts: 'dangerously',
        pretendToBeVisual: true,
        beforeParse(window) {
          window.fetch = (url, opts) => Promise.resolve(route(url, opts));
          window.confirm = () => true;
          window.alert = () => {};
          window.scrollTo = () => {};
          window.requestAnimationFrame = cb => window.setTimeout(() => cb(Date.now()), 0);
          window.cancelAnimationFrame = id => window.clearTimeout(id);
          window.HTMLMediaElement.prototype.play = () => Promise.resolve();
          window.HTMLMediaElement.prototype.pause = () => {};
        },
      });
      await waitFor(() => dom.window.document.querySelector('#dsp-editor-panel'));
      return dom;
    },
  };
}

async function waitFor(predicate, { timeoutMs = 4000, intervalMs = 20 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  throw new Error('Timed out waiting for condition');
}

async function main() {
  const harness = createHarness();
  const dom = await harness.createDom();

  try {
    const { document } = dom.window;

    const duplicateActive = await waitFor(() => document.querySelector('#dsp-duplicate-active-btn'));
    duplicateActive.click();

    await waitFor(() => harness.dspProfiles.some(profile => !profile.is_factory && profile.name.includes('Custom')));
    const clonedProfile = harness.dspProfiles.find(profile => !profile.is_factory && profile.name.includes('Black Background'));
    assert.ok(clonedProfile, 'expected a cloned custom preset');

    const editorName = await waitFor(() => document.querySelector('#dsp-editor-name'));
    editorName.value = 'Night Focus';
    document.querySelector('#dsp-editor-description').value = 'Custom voicing for quiet listening';
    document.querySelector('#dsp-editor-preamp').value = '-2.5';
    document.querySelector('#dsp-add-filter-btn').click();

    const filterRow = await waitFor(() => document.querySelector('#dsp-filter-list .dsp-filter-row'));
    filterRow.querySelector('.dsp-filter-type').value = 'high_shelf';
    filterRow.querySelector('.dsp-filter-freq').value = '9200';
    filterRow.querySelector('.dsp-filter-gain').value = '1.3';
    filterRow.querySelector('.dsp-filter-q').value = '0.8';

    document.querySelector('#dsp-save-btn').click();
    await waitFor(() => harness.dspProfiles.some(profile => profile.name === 'Night Focus'));

    const savedProfile = harness.dspProfiles.find(profile => profile.name === 'Night Focus');
    assert.ok(savedProfile, 'expected saved custom profile');
    assert.equal(savedProfile.preamp_db, -2.5);
    assert.match(savedProfile.filter_chain_json, /"high_shelf"/);

    await waitFor(() => document.querySelector(`#dsp-preset-grid .dsp-preset-card[data-profile-id="${savedProfile.id}"]`));
    await waitFor(() =>
      document.querySelector('#dsp-editor-name')?.value === 'Night Focus' &&
      document.querySelector('#dsp-editor-status')?.textContent?.includes('Editing a custom preset.')
    );
    document.querySelector('#dsp-apply-editor-btn').click();
    await waitFor(() => document.querySelector('#dsp-select').value === String(savedProfile.id));

    const activeCard = await waitFor(() => document.querySelector(`.dsp-preset-card[data-profile-id="${savedProfile.id}"]`));
    assert.equal(activeCard.style.borderColor, 'var(--gold)');

    document.querySelector('#mobile-top-upsample').click();
    const upsample4xButton = await waitFor(() =>
      Array.from(document.querySelectorAll('.mobile-select-picker-option'))
        .find(button => button.textContent?.trim() === '4×')
    );
    upsample4xButton.click();
    await waitFor(() => document.querySelector('#upsample-select').value === '4x');
    assert.equal(document.querySelector('#mobile-top-upsample-label').textContent.trim(), '4×');

    document.querySelector('#mobile-top-dsp').click();
    const blackBackgroundButton = await waitFor(() =>
      Array.from(document.querySelectorAll('.mobile-select-picker-option'))
        .find(button => button.textContent?.trim() === 'Black')
    );
    blackBackgroundButton.click();
    await waitFor(() => document.querySelector('#dsp-select').value === '2');
    assert.equal(document.querySelector('#mobile-top-dsp-label').textContent.trim(), 'Black');

    const requestCountBeforeEqSave = harness.requests.length;
    dom.window.handleEQSliderInput(0, '2.0');
    document.querySelector('#eq-btn-save').click();
    await waitFor(() => harness.requests.length > requestCountBeforeEqSave);
    const eqSaveRequests = harness.requests.slice(requestCountBeforeEqSave);
    assert.ok(
      eqSaveRequests.some(request =>
        request.method === 'POST' || request.method === 'PUT'
          ? /^\/api\/dsp\/profiles(?:\/\d+)?$/.test(request.path)
          : false
      ),
      'expected graphic EQ save to use the current /api/dsp/profiles endpoint'
    );
    assert.ok(
      eqSaveRequests.every(request => request.path !== '/api/dsp/presets/custom' && request.path !== '/api/dsp/apply'),
      'expected graphic EQ save to avoid deprecated DSP endpoints'
    );

    document.querySelector('#dsp-delete-editor-btn').click();
    await waitFor(() => !harness.dspProfiles.some(profile => profile.id === savedProfile.id));

    assert.ok(
      harness.requests.some(request => request.method === 'POST' && /\/api\/dsp\/profiles\/\d+\/clone$/.test(request.path)),
      'expected clone request to be issued'
    );
    assert.ok(
      harness.requests.some(request => request.method === 'POST' && request.path === '/api/dsp/profiles'),
      'expected custom profile save request to be issued'
    );
    assert.ok(
      harness.requests.some(request => request.method === 'DELETE' && request.path === `/api/dsp/profiles/${savedProfile.id}`),
      'expected custom profile delete request to be issued'
    );
  } finally {
    dom.window.close();
  }
}

try {
  await main();
  console.log('frontend_dsp_settings_check=OK');
  process.exit(0);
} catch (error) {
  console.error('frontend_dsp_settings_check=FAILED');
  console.error(error);
  process.exit(1);
}
