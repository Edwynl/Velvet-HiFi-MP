import { readFileSync } from 'node:fs';
import { inlineFrontendModules } from './inline-modules.mjs';
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

// Parametric/shelf preset with a disabled filter and an out-of-order chain:
// the graphic EQ must keep every field and the order exactly.
const STUDIO_PEQ_FILTERS = [
  { type: 'high_shelf', freq: 9500, gain: 1.8, q: 0.65, enabled: true },
  { type: 'peaking', freq: 47, gain: 2.25, q: 4.3, enabled: true },
  { type: 'low_shelf', freq: 110, gain: -1.4, q: 0.8, enabled: false },
  { type: 'peaking', freq: 3150, gain: -2.5, q: 6, enabled: true },
];

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
    {
      id: 4,
      name: 'Studio PEQ',
      description: 'Custom parametric voicing.',
      category: 'custom',
      is_factory: 0,
      preamp_db: -3.5,
      stereo_width: 12,
      filter_chain_json: JSON.stringify(STUDIO_PEQ_FILTERS),
      tags: JSON.stringify(['custom']),
      sort_order: 999,
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

    if (parsedUrl.pathname.startsWith('/api/cast/control/') && method === 'POST') {
      return makeJsonResponse({ success: true });
    }

    if (parsedUrl.pathname.startsWith('/api/cast/status/') && method === 'GET') {
      return makeJsonResponse({ state: 'PLAYING', position: 5, duration: 120, is_playing: true });
    }

    // Mirrors server.cast_to_device: an active preset gets the DSP transcode.
    if (parsedUrl.pathname.startsWith('/api/cast/') && method === 'POST') {
      const requested = body?.dsp_profile_id || null;
      return makeJsonResponse({
        success: true,
        start_offset: requested ? (body.start_seconds || 0) : 0,
        dsp: {
          requested_profile_id: requested,
          effective_profile_id: requested,
          status: requested ? 'applied' : 'off',
          stream: requested ? 'dsp_transcode' : 'direct',
          disabled_filters: [],
        },
      });
    }

    if (parsedUrl.pathname === '/api/playback/stop' && method === 'POST') {
      return makeJsonResponse({ success: true });
    }

    throw new Error(`Unhandled fetch: ${method} ${parsedUrl.pathname}`);
  }

  return {
    requests,
    dspProfiles,
    async createDom() {
      const html = inlineFrontendModules(readFileSync(HTML_PATH, 'utf8'))
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
    assert.equal(dom.window.localStorage.getItem('velvet:dsp-profile-id'), '2');
    assert.equal(dom.window.localStorage.getItem('velvet:dsp-last-profile-id'), '2');

    // Simulate a transient runtime fallback that clears active DSP only;
    // load should restore from the last selected DSP preference.
    dom.window.localStorage.removeItem('velvet:dsp-profile-id');
    dom.window.eval("state.dspProfile = ''; state.lastEqProfile = '';");
    await dom.window.loadDspProfiles();
    await waitFor(() => document.querySelector('#dsp-select').value === '2');

    // An explicit Off (user choice or Auto Fallback) must survive a profile
    // reload instead of resurrecting the last preset.
    dom.window.eval("state.dspProfile = ''; persistDspProfileId('', { lastProfileId: '2' });");
    await dom.window.loadDspProfiles();
    assert.equal(document.querySelector('#dsp-select').value, '');
    assert.equal(dom.window.eval('state.dspProfile'), '');
    assert.equal(dom.window.localStorage.getItem('velvet:dsp-last-profile-id'), '2');
    dom.window.eval("state.dspProfile = '2'; persistDspProfileId('2');");
    await dom.window.loadDspProfiles();
    await waitFor(() => document.querySelector('#dsp-select').value === '2');

    await dom.window.openGraphicEQ();
    await waitFor(() => document.querySelector('#eq-current-name')?.textContent?.trim() === 'Black Background');
    const requestCountBeforeFactoryApply = harness.requests.length;
    const profileCountBeforeFactoryApply = harness.dspProfiles.length;
    document.querySelector('#eq-btn-save').click();
    await waitFor(() => document.querySelector('#dsp-select').value === '2');
    assert.equal(
      harness.dspProfiles.length,
      profileCountBeforeFactoryApply,
      'expected applying an unchanged factory EQ to avoid creating a custom profile'
    );
    assert.ok(
      harness.requests
        .slice(requestCountBeforeFactoryApply)
        .every(request => request.method !== 'POST' && request.method !== 'PUT'),
      'expected applying an unchanged factory EQ to avoid DSP profile mutations'
    );
    await dom.window.openGraphicEQ();
    await waitFor(() => document.querySelector('#eq-current-name')?.textContent?.trim() === 'Black Background');
    document.querySelector('#eq-overlay')?.classList.remove('active');

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
    // Black Background is a shelf preset: a slider edit saves a converted
    // graphic copy and never touches the factory preset.
    const createdEqProfile = harness.dspProfiles.find(profile =>
      !profile.is_factory && profile.name === 'Black Background (Graphic)' && /graphic-eq/.test(profile.tags || '')
    );
    assert.ok(createdEqProfile, 'expected graphic EQ save to create a custom EQ profile');
    assert.equal(harness.dspProfiles.find(profile => profile.id === 2).filter_chain_json,
      JSON.stringify([{ type: 'low_shelf', freq: 90, gain: -0.8, q: 0.7, enabled: true }]));

    await dom.window.loadDspProfiles();
    await dom.window.selectEQPreset(String(createdEqProfile.id));
    const profileCountBeforeEqUpdate = harness.dspProfiles.length;
    const requestCountBeforeEqUpdate = harness.requests.length;
    dom.window.handleEQSliderInput(1, '-1.0');
    document.querySelector('#eq-btn-save').click();
    await waitFor(() =>
      harness.requests
        .slice(requestCountBeforeEqUpdate)
        .some(request => request.method === 'PUT' && request.path === `/api/dsp/profiles/${createdEqProfile.id}`)
    );
    assert.ok(
      harness.requests
        .slice(requestCountBeforeEqUpdate)
        .some(request => request.method === 'PUT' && request.path === `/api/dsp/profiles/${createdEqProfile.id}`),
      'expected editing an existing custom EQ to update it instead of creating another profile'
    );
    assert.equal(
      harness.dspProfiles.length,
      profileCountBeforeEqUpdate,
      'expected existing custom EQ save to avoid duplicate custom profiles'
    );

    // The graphic EQ save may change which profile is loaded in the editor.
    // Delete the exact custom preset created above, not whichever preset
    // happens to be selected in the editor at this moment: load it into the
    // editor through its own card first (cards no longer carry Delete).
    const customEditButton = await waitFor(() =>
      document.querySelector(`.dsp-open-btn[data-profile-id="${savedProfile.id}"]`)
    );
    customEditButton.click();
    await waitFor(() => document.querySelector('#dsp-delete-editor-btn')?.style.display === 'inline-flex');
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

    // EQ-1: headroom comes from the combined response, not the largest slider,
    // and is never capped at -12 dB.
    dom.window.selectEQPreset('');
    [4, 5, 6].forEach(band => dom.window.handleEQSliderInput(band, '12'));
    const peak = dom.window.eval('dspWorstCaseHeadroom(graphicEqFilters()).peakDb');
    const preamp = dom.window.eval('recommendedGraphicEqPreamp()');
    assert.ok(peak > 13, `expected stacked +12 dB bands to peak above 13 dB, got ${peak}`);
    assert.ok(preamp <= -(peak + 1) && preamp > -(peak + 1.11),
      `expected preamp to cover the combined peak plus 1 dB margin, got ${preamp} for ${peak}`);
    assert.ok(preamp < -12, 'expected no -12 dB headroom cap');
    // The note is debounced while sliders move.
    await waitFor(() => /EQ peak \+1\d\.\d dB, worst case/.test(document.querySelector('#eq-headroom-note').textContent));

    // Codex review 2: the same curve peaks +15.16 dB at 48 kHz but +16.96 dB at
    // 192 kHz, so a preamp from 48 kHz alone (-16.2 dB) clipped at 192 kHz.
    const codexGains = [4, -12, -4, 4, 12, 0, 0, 12, 12, 12];
    codexGains.forEach((gain, band) => dom.window.handleEQSliderInput(band, String(gain)));
    const at48k = dom.window.eval('dspPeakGainDb(graphicEqFilters(), 48000)');
    const at192k = dom.window.eval('dspPeakGainDb(graphicEqFilters(), 192000)');
    assert.ok(Math.abs(at48k - 15.163) < 0.01 && Math.abs(at192k - 16.958) < 0.01, `${at48k} / ${at192k}`);
    const codexPreamp = dom.window.eval('recommendedGraphicEqPreamp()');
    assert.ok(codexPreamp <= -(at192k + 1), `expected 192 kHz to be covered, got ${codexPreamp}`);
    assert.equal(codexPreamp, -18.1);
    // Same model as dsp_engine.py (FFmpeg-verified there): a single +6 dB
    // peaking band peaks at exactly +6 dB at its centre frequency.
    const single = dom.window.eval(
      "dspFiltersResponseDb([{type:'peaking',freq:1000,gain:6,q:1.41}], 48000, [1000])[0]");
    assert.ok(Math.abs(single - 6) < 0.01, `expected +6 dB at 1 kHz, got ${single}`);
    const shelfPeak = dom.window.eval(
      "dspPeakGainDb([{type:'low_shelf',freq:100,gain:4,q:0.7}], 48000)");
    assert.ok(shelfPeak > 4 && shelfPeak < 4.6, `expected FFmpeg shelf overshoot, got ${shelfPeak}`);
    // Codex review repro: FFmpeg peaks at +32.52 dB; a plain grid found +28.36 dB.
    const resonantPeak = dom.window.eval(
      "dspPeakGainDb([{type:'high_shelf',freq:19900,gain:12,q:10}], 44100)");
    assert.ok(resonantPeak > 32.4, `expected resonant shelf peak above 32.4 dB, got ${resonantPeak}`);

    // EQ-1: "Off" never claims bit-perfect output, and casting never claims
    // that DSP or upsampling is applied by a DLNA renderer.
    assert.ok(
      !Array.from(document.querySelectorAll('div')).some(el => /^(Off \/ )?Bit-perfect$/i.test(el.textContent.trim())),
      'Off must not be labelled bit-perfect');
    assert.match(document.body.textContent, /Source Direct \/ VELVET DSP Off/);
    const castLines = dom.window.describeSignalProcessing('cast', { name: 'Black Background' }, '4x');
    assert.ok(castLines.some(line => /not applied on this renderer/.test(line)));
    assert.ok(!castLines.some(line => /^DSP Black Background$/.test(line)));
    assert.ok(castLines.some(line => /upsampling not applied/.test(line)));
    const bypassed = dom.window.describeSignalProcessing('native', { name: 'Empty' }, 'none', { active: false });
    assert.match(bypassed[1], /selected but bypassed/);
    const applied = dom.window.describeSignalProcessing('browser', { name: 'Hot' }, 'none',
      { active: true, disabled_filters: [{}], eq_clipping_risk: true, net_peak_db: 2.5 });
    assert.equal(applied[1], 'DSP Hot');
    assert.ok(applied.some(line => /above Nyquist disabled/.test(line)));
    assert.ok(applied.some(line => /EQ may clip: estimated peak \+2\.5 dB/.test(line)));
    const guarded = dom.window.describeSignalProcessing('native', { name: 'Hot' }, 'none',
      { active: true, headroom_guard_db: -13, effective_preamp_db: -13 });
    assert.ok(guarded.some(line => /Headroom guard: -13\.0 dB extra attenuation/.test(line)));
    const browserLines = dom.window.describeSignalProcessing('browser', null, 'none');
    assert.equal(JSON.stringify(browserLines), JSON.stringify(['No PCM upsampling', 'VELVET DSP off (source direct)']));

    // EQ-3: each output zone keeps its own DSP choice. The browser keeps the
    // legacy keys, a USB output inherits them until chosen, a renderer starts Off.
    const dspSelect = document.querySelector('#dsp-select');
    const chooseDsp = (value) => {
      dspSelect.value = value;
      dspSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    };
    const renderer = { type: 'cast', id: 'uuid:renderer', udn: 'uuid:renderer', name: 'EXN100' };
    const usbDac = { type: 'native', id: 4, device_id: 4, name: 'USB DAC' };
    const browserZone = { type: 'browser', id: 'browser', name: 'Local Web Player' };
    const zoneMap = () => JSON.parse(dom.window.localStorage.getItem('velvet:dsp-zone-profiles') || '{}');
    dom.window.eval('state.currentTrack = null;');
    chooseDsp('2');
    await dom.window.activateZone(renderer, false);
    assert.equal(dom.window.eval('state.dspProfile'), '', 'a renderer never chosen for starts Off');
    assert.equal(dspSelect.value, '');
    assert.equal(dom.window.eval('isDspExplicitlyOff()'), true);
    chooseDsp('3');
    assert.equal(zoneMap()['cast:uuid:renderer'], '3');
    assert.equal(dom.window.localStorage.getItem('velvet:dsp-profile-id'), '2', 'browser choice untouched');
    await dom.window.activateZone(usbDac, false);
    assert.equal(dom.window.eval('state.dspProfile'), '2', 'a USB output inherits the browser choice');
    chooseDsp('');
    assert.equal(zoneMap()['native:USB DAC'], 'off');
    await dom.window.activateZone(browserZone, false);
    assert.equal(dom.window.eval('state.dspProfile'), '2');
    assert.equal(dspSelect.value, '2');
    await dom.window.activateZone(usbDac, false);
    assert.equal(dom.window.eval('state.dspProfile'), '');
    await dom.window.activateZone(renderer, false);
    assert.equal(dom.window.eval('state.dspProfile'), '3');
    assert.equal(dspSelect.value, '3');
    assert.equal(dom.window.eval('isDspExplicitlyOff()'), false);

    // Upsampling never reaches a renderer: changing it must not re-cast.
    dom.window.eval("state.currentTrack = { id: 1, title: 'Cast Track', duration: 180 }; state.castPosition = 42;");
    let requestsBefore = harness.requests.length;
    const upsampleSelect = document.querySelector('#upsample-select');
    upsampleSelect.value = '2x';
    upsampleSelect.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(!harness.requests.slice(requestsBefore).some(request => request.path.startsWith('/api/cast/')),
      'expected no re-cast when upsampling changes in a cast zone');
    assert.equal(dom.window.eval('state.upsample'), '2x');

    // A DSP change re-casts the renderer's stream from where it is now.
    requestsBefore = harness.requests.length;
    chooseDsp('2');
    await waitFor(() => dom.window.eval('state.castDsp?.requested_profile_id') === 2);
    const recast = harness.requests.slice(requestsBefore).find(request => request.path === '/api/cast/uuid:renderer');
    assert.deepEqual(recast.body, { track_id: 1, dsp_profile_id: 2, start_seconds: 42 });
    assert.equal(dom.window.eval('state.castStartOffset'), 42);
    const castDsp = dom.window.eval('state.castDsp');
    const blackBackground = { id: 2, name: 'Black Background' };
    const castApplied = dom.window.describeSignalProcessing('cast', blackBackground, '2x',
      { active: true, headroom_guard_db: -2, effective_preamp_db: -6 }, castDsp);
    assert.match(castApplied[0], /^VELVET DSP stream to renderer \(24-bit FLAC/);
    assert.ok(castApplied.includes('DSP Black Background'), castApplied.join(' | '));
    assert.ok(castApplied.some(line => /Headroom guard: -2\.0 dB/.test(line)));
    assert.ok(castApplied.some(line => /upsampling not applied/.test(line)));
    assert.deepEqual({ ...dom.window.describeZoneDsp('cast', blackBackground, null, castDsp) },
      { requested: 'Black Background', effective: 'Black Background' });
    // Selected but not yet on the renderer (e.g. the re-cast failed): never claimed.
    const pending = dom.window.describeSignalProcessing('cast', { id: 3, name: 'Vocal Forward Warm' }, 'none', null, castDsp);
    assert.ok(pending.some(line => /Vocal Forward Warm not applied on this renderer yet/.test(line)));
    assert.match(dom.window.describeZoneDsp('cast', { id: 3, name: 'Vocal Forward Warm' }, null, castDsp).effective,
      /Black Background \(previous selection\)/);
    // Off re-casts the direct source.
    chooseDsp('');
    await waitFor(() => dom.window.eval('state.castDsp?.status') === 'off');
    assert.equal(dom.window.eval('state.castStartOffset'), 0);
    assert.equal(dom.window.describeSignalProcessing('cast', null, 'none', null, dom.window.eval('state.castDsp'))[0],
      'Direct source stream to renderer');
    dom.window.eval('stopCastPolling(); state.currentTrack = null;');
    await dom.window.activateZone(browserZone, false);
  } finally {
    dom.window.close();
  }
}

// The curve path, read back as (frequency, dB) on the plot's own axes.
function readCurve(document, selector = '#eq-curve-path') {
  const range = Number(document.querySelector('#eq-curve-svg').dataset.rangeDb);
  const d = document.querySelector(selector).getAttribute('d') || '';
  return [...d.matchAll(/[ML] (-?[\d.]+) (-?[\d.]+)/g)].map(([, x, y]) => ({
    freq: 20 * Math.pow(1000, Number(x) / 1000),
    db: (150 - Number(y)) / 150 * range,
  }));
}

function modelDb(dom, filters, freqs) {
  dom.window.__eqFilters = filters;
  dom.window.__eqFreqs = freqs;
  return dom.window.eval('dspFiltersResponseDb(window.__eqFilters, 48000, window.__eqFreqs)');
}

function assertCurveIsModel(dom, filters, offsetDb = 0, selector = '#eq-curve-path') {
  const points = readCurve(dom.window.document, selector);
  assert.ok(points.length >= 256, `expected a dense curve, got ${points.length} points`);
  const model = modelDb(dom, filters, points.map(p => p.freq));
  points.forEach((point, i) => {
    assert.ok(Math.abs(point.db - (model[i] + offsetDb)) < 0.02,
      `${selector} at ${point.freq.toFixed(1)} Hz: drawn ${point.db.toFixed(3)} dB, model ${(model[i] + offsetDb).toFixed(3)} dB`);
  });
  return points;
}

function preampLineDb(document) {
  const range = Number(document.querySelector('#eq-curve-svg').dataset.rangeDb);
  return (150 - Number(document.querySelector('#eq-preamp-line').getAttribute('y1'))) / 150 * range;
}

// EQ-2: exact curve, lossless graphic/parametric round trip, A/B, Bypass,
// Reset, Duplicate, Save as and the Preview/Applied state.
async function eqEditorFidelityChecks() {
  const harness = createHarness();
  const dom = await harness.createDom();
  const { document } = dom.window;
  const status = () => document.querySelector('#eq-apply-state').textContent.trim();
  const draft = () => JSON.parse(dom.window.eval('JSON.stringify(eqActiveDraft())'));
  const writes = since => harness.requests.slice(since).filter(r => r.method === 'POST' || r.method === 'PUT');
  try {
    await dom.window.loadDspProfiles();
    await dom.window.openGraphicEQ();

    // Showing a parametric preset never rewrites its filters.
    await dom.window.selectEQPreset('4');
    assert.deepEqual(draft().filters, STUDIO_PEQ_FILTERS);
    assert.equal(draft().edited, false);
    assert.match(document.querySelector('#eq-conversion-note').textContent, /Parametric preset/);
    assert.equal(document.querySelector('#eq-conversion-note').hidden, false);
    assert.equal(document.querySelector('#eq-btn-save').textContent, 'Apply');
    assert.equal(status(), 'Preview (not yet applied)');

    // The curve is the real cascade (shelf Q*sqrt(2) as in FFmpeg), not a
    // per-band heuristic; the narrow 47 Hz peak is drawn at its true height.
    const points = assertCurveIsModel(dom, STUDIO_PEQ_FILTERS);
    const at47 = modelDb(dom, STUDIO_PEQ_FILTERS, [47])[0];
    assert.ok(Math.max(...points.map(p => p.db)) >= at47 - 0.01, 'narrow peak must not be missed');
    dom.window.__eqFilters = STUDIO_PEQ_FILTERS;
    const effective = dom.window.eval('dspEffectivePreampDb(window.__eqFilters, -3.5)');
    assert.equal(effective, -3.5, 'saved preamp covers this preset, so the server keeps it');
    assert.ok(Math.abs(preampLineDb(document) - effective) < 0.02, 'preamp line at the effective preamp');
    assert.match(document.querySelector('#eq-preamp-label').textContent, /Preamp -3\.5 dB/);
    // A hot preset: the line shows the guarded preamp the server applies,
    // not the insufficient saved one.
    const guarded = dom.window.eval(
      "dspEffectivePreampDb([{type:'peaking',freq:1000,gain:9,q:1,enabled:true}], -2)");
    assert.equal(guarded, -10, `expected the playback guard preamp, got ${guarded}`);

    // An untouched preset applies unchanged: no write at all.
    const beforeApply = harness.requests.length;
    document.querySelector('#eq-btn-save').click();
    await waitFor(() => document.querySelector('#dsp-select').value === '4');
    assert.deepEqual(writes(beforeApply), []);
    assert.equal(harness.dspProfiles.find(p => p.id === 4).filter_chain_json, JSON.stringify(STUDIO_PEQ_FILTERS));
    await dom.window.openGraphicEQ();
    assert.equal(status(), 'Applied');

    // Save as... on an untouched preset keeps every filter field and the order.
    dom.window.prompt = () => 'Studio PEQ Copy';
    document.querySelector('#eq-btn-save-as').click();
    const copy = await waitFor(() => harness.dspProfiles.find(p => p.name === 'Studio PEQ Copy'));
    assert.deepEqual(JSON.parse(copy.filter_chain_json), STUDIO_PEQ_FILTERS);
    assert.equal(copy.preamp_db, -3.5);
    assert.equal(copy.stereo_width, 12);
    await waitFor(() => document.querySelector('#eq-current-name').textContent === 'Studio PEQ Copy');
    assert.equal(status(), 'Preview (not yet applied)', 'a saved copy is not applied yet');
    assert.equal(document.querySelector('#dsp-select').value, '4');

    // Duplicate of an untouched factory preset is a server-side exact copy.
    await dom.window.selectEQPreset('2');
    const beforeDuplicate = harness.requests.length;
    document.querySelector('#eq-btn-duplicate').click();
    await waitFor(() => harness.dspProfiles.some(p => p.name === 'Black Background Copy'));
    assert.ok(writes(beforeDuplicate).some(r => r.path === '/api/dsp/profiles/2/clone'));
    assert.equal(harness.dspProfiles.find(p => p.name === 'Black Background Copy').filter_chain_json,
      harness.dspProfiles.find(p => p.id === 2).filter_chain_json);

    // Editing a shelf preset in graphic mode converts an explicit copy.
    await dom.window.selectEQPreset('2');
    const shelfFilters = JSON.parse(harness.dspProfiles.find(p => p.id === 2).filter_chain_json);
    const fit = dom.window.eval(
      'const d = eqActiveDraft(); [dspFiltersResponseDb(graphicEqFilters(d.gains), 48000, GEQ_BANDS), dspFiltersResponseDb(d.filters, 48000, GEQ_BANDS)]');
    fit[0].forEach((db, i) => assert.ok(Math.abs(db - fit[1][i]) < 0.2, `band ${i}: fit ${db} vs ${fit[1][i]}`));
    dom.window.handleEQSliderInput(3, '1.5');
    assert.equal(draft().converted, true);
    assert.equal(draft().name, 'Black Background (Graphic)');
    assert.ok(draft().filters.every(f => f.type === 'peaking' && f.q === 1.41));
    assert.match(document.querySelector('#eq-conversion-note').textContent, /graphic copy of "Black Background"/);
    assert.equal(document.querySelector('#eq-btn-save').textContent, 'Save as Custom');
    assert.deepEqual(JSON.parse(harness.dspProfiles.find(p => p.id === 2).filter_chain_json), shelfFilters);
    assertCurveIsModel(dom, draft().filters);
    // Reset discards the copy and restores the exact preset.
    document.querySelector('#eq-btn-reset').click();
    assert.deepEqual(draft().filters, shelfFilters);
    assert.equal(draft().converted, false);
    assert.equal(draft().edited, false);
    assert.equal(document.querySelector('#eq-btn-save').textContent, 'Apply');

    // Editing never re-renders album artwork.
    dom.window.eval("state.currentTrack = { id: 1, album_id: 7, title: 'T', artist_name: 'A', format: 'flac' }; updatePlayerUI(state.currentTrack);");
    const artMutations = [];
    const observer = new dom.window.MutationObserver(records => artMutations.push(...records));
    ['player-art', 'mobile-player-cover', 'mobile-player-bg-image'].forEach(id => {
      const el = document.getElementById(id);
      if (el) observer.observe(el, { attributes: true, childList: true, subtree: true });
    });
    await dom.window.selectEQPreset('4');
    dom.window.handleEQSliderInput(5, '2');
    document.querySelector('#eq-btn-ab').click();
    dom.window.handleEQSliderInput(6, '-3');
    document.querySelector('#eq-btn-ab').click();
    document.querySelector('#eq-btn-reset').click();
    await new Promise(resolve => setTimeout(resolve, 200));
    observer.disconnect();
    assert.deepEqual(artMutations.map(r => r.target.id || r.target.tagName), []);
    dom.window.eval('state.currentTrack = null;');

    // A/B: two slots; the other one is drawn level-matched by its preamp.
    await dom.window.openGraphicEQ();
    assert.equal(document.querySelector('#eq-btn-ab').textContent, 'A/B: A');
    assert.equal(document.querySelector('#eq-curve-ghost').getAttribute('d'), '');
    document.querySelector('#eq-btn-ab').click();
    assert.equal(document.querySelector('#eq-btn-ab').textContent, 'A/B: B');
    assert.deepEqual(draft().filters, STUDIO_PEQ_FILTERS, 'B starts as a copy of A');
    assert.equal(status(), 'Applied');
    await dom.window.selectEQPreset('');
    assert.equal(status(), 'Preview (not yet applied)');
    // B is flat at 0 dB preamp; A is drawn at its net level, EQ - 3.5 dB.
    assertCurveIsModel(dom, STUDIO_PEQ_FILTERS, -3.5, '#eq-curve-ghost');
    dom.window.handleEQSliderInput(4, '6');
    await waitFor(() => /Auto headroom/.test(document.querySelector('#eq-headroom-note').textContent));
    const bPreamp = dom.window.eval('eqDraftHeadroom(eqActiveDraft()).effectivePreampDb');
    assert.ok(bPreamp < -6, `expected B to get headroom, got ${bPreamp}`);
    assert.ok(Math.abs(preampLineDb(document) - bPreamp) < 0.02);
    assertCurveIsModel(dom, STUDIO_PEQ_FILTERS, -3.5 - bPreamp, '#eq-curve-ghost');
    document.querySelector('#eq-btn-ab').click();
    assert.deepEqual(draft().filters, STUDIO_PEQ_FILTERS, 'editing B leaves A untouched');
    assert.equal(status(), 'Applied');
    assert.notEqual(dom.window.eval('currentEqGains[4]'), 6, 'A shows its own sliders');
    document.querySelector('#eq-btn-ab').click();
    assert.equal(status(), 'Preview (not yet applied)');
    const beforeSaveB = harness.requests.length;
    document.querySelector('#eq-btn-save').click();
    await waitFor(() => writes(beforeSaveB).length && document.querySelector('#dsp-select').value !== '4');
    const savedB = harness.dspProfiles.find(p => String(p.id) === document.querySelector('#dsp-select').value);
    assert.equal(savedB.name, 'Custom EQ');
    assert.deepEqual(JSON.parse(savedB.filter_chain_json),
      [{ type: 'peaking', freq: 500, gain: 6, q: 1.41, enabled: true }]);
    assert.equal(savedB.preamp_db, bPreamp);

    // Bypass: Off now, the applied preset back on the second press or on close.
    await dom.window.openGraphicEQ();
    assert.equal(status(), 'Applied');
    document.querySelector('#eq-btn-bypass').click();
    assert.equal(document.querySelector('#dsp-select').value, '');
    assert.equal(status(), 'Bypassed (source direct)');
    assert.equal(document.querySelector('#eq-btn-bypass').getAttribute('aria-pressed'), 'true');
    document.querySelector('#eq-btn-bypass').click();
    assert.equal(document.querySelector('#dsp-select').value, String(savedB.id));
    assert.equal(status(), 'Applied');
    document.querySelector('#eq-btn-bypass').click();
    document.querySelector('#eq-close-btn').click();
    assert.equal(document.querySelector('#dsp-select').value, String(savedB.id), 'closing ends the bypass');
  } finally {
    dom.window.close();
  }
}

try {
  await main();
  await eqEditorFidelityChecks();
  console.log('frontend_dsp_settings_check=OK');
  process.exit(0);
} catch (error) {
  console.error('frontend_dsp_settings_check=FAILED');
  console.error(error);
  process.exit(1);
}
