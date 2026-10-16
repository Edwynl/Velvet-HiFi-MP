/* VELVET settings-view: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
async function renderSettings(vc) {
  const [config, scanStatus, dspProfiles] = await Promise.all([
    api('/config'), api('/scan/status'), api('/dsp/profiles')
  ]);
  const settingsDspProfiles = dspProfiles || [];
  const activeDspId = String(state.dspProfile || '');
  const dspCategoryLabels = {
    reference: 'Reference',
    correction: 'Correction',
    taste: 'Taste',
    genre: 'Genre',
    custom: 'My Presets',
  };
  const dspCategoryOrder = ['reference', 'correction', 'taste', 'genre', 'custom'];
  const renderDspPresetCard = (profile) => `
    <div class="dsp-preset-card" data-profile-id="${profile.id}" style="background:var(--s2);border:1px solid ${String(profile.id) === activeDspId ? 'var(--gold)' : 'var(--border)'};border-radius:var(--radius);padding:14px 16px;margin-bottom:10px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
        <div>
          <div style="font-size:14px;color:var(--text);font-weight:600">${escHtml(profile.name)}</div>
          <div style="font-size:11px;color:var(--gold);text-transform:uppercase;letter-spacing:.08em;margin-top:4px">${escHtml(profile.category || 'custom')}</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
          ${String(profile.id) === activeDspId ? '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(201,168,76,.18);color:var(--gold)">Active</span>' : ''}
          ${profile.is_factory ? '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,.06);color:var(--muted)">Factory</span>' : '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,.06);color:var(--muted)">Custom</span>'}
        </div>
      </div>
      <div style="font-size:12px;color:var(--muted);line-height:1.55;margin-top:8px">${escHtml(profile.description || 'No description')}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
        <button class="btn btn-ghost dsp-apply-btn" data-profile-id="${profile.id}" style="padding:8px 10px">Apply</button>
        <button class="btn btn-ghost dsp-open-btn" data-profile-id="${profile.id}" style="padding:8px 10px">Open</button>
        ${profile.is_factory
          ? `<button class="btn btn-ghost dsp-clone-btn" data-profile-id="${profile.id}" style="padding:8px 10px;color:var(--gold)">Duplicate</button>`
          : `<button class="btn btn-ghost dsp-delete-btn" data-profile-id="${profile.id}" style="padding:8px 10px;color:#ff8d8d">Delete</button>`}
      </div>
    </div>
  `;
  const renderDspPresetGroups = () => dspCategoryOrder
    .map(category => {
      const profiles = settingsDspProfiles.filter(profile => (profile.category || 'custom') === category);
      if (!profiles.length) return '';
      return `
        <div class="dsp-category-heading">${dspCategoryLabels[category] || category}</div>
        ${profiles.map(renderDspPresetCard).join('')}
      `;
    })
    .join('');

  vc.innerHTML = `
    <div class="topbar">
      ${getTopNavHtml('settings')}
    </div>
    <div class="section-header">
      <div class="section-title">Settings</div>
      <div class="section-sub">Configure your VELVET server</div>
    </div>

    <div class="settings-form">
      <div class="form-group">
        <label class="form-label">Music Library Folders</label>
        <div id="libraries-list" style="display:flex;flex-direction:column;gap:12px;margin-bottom:16px">
          ${config.libraries.map(lib => `
            <div class="library-item" style="display:flex;align-items:center;background:var(--s2);border:1px solid var(--border);border-radius:var(--radius);padding:10px 16px">
              <div style="flex:1;font-size:13px;color:var(--text);font-family:'DM Mono',monospace">${escHtml(lib.path)}</div>
              <button class="btn btn-ghost refresh-lib" data-path="${escHtml(lib.path)}" data-id="${lib.id}" style="padding:6px 10px;color:var(--gold)" title="Re-read metadata from files in this folder">Re-read Tags</button>
              <button class="btn btn-ghost remove-lib" data-id="${lib.id}" style="padding:6px 10px;color:var(--muted)">Remove</button>
            </div>
          `).join('')}
          ${!config.libraries.length ? '<div style="color:var(--muted);font-size:13px">No folders added yet.</div>' : ''}
        </div>
        
        <div style="display:flex;gap:10px;margin-bottom:8px">
          <input class="form-input" id="music-dir-input" type="text"
                 style="flex:1;margin-bottom:0"
                 placeholder="e.g. D:\\Music">
          <button class="btn btn-gold" id="add-lib-btn" style="white-space:nowrap">Add Folder</button>
        </div>
        <div class="form-hint">Path to your music folder (2–3TB supported). All subfolders will be scanned.</div>
      </div>

      <div style="display:flex;gap:10px;margin-bottom:32px">
        <button class="btn btn-gold" id="save-and-scan">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px">
            <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>
          </svg>
          Full Rescan
        </button>
        <button class="btn btn-ghost" id="reset-library-btn" style="color:#ff6b6b;font-weight:600">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;margin-right:4px">
            <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
          </svg>
          Reset Library
        </button>
      </div>

      ${scanStatus.running ? renderScanProgress(scanStatus) : ''}

      <div id="scan-status-panel">
        ${!scanStatus.running && scanStatus.finished_at ? `
          <div style="background:var(--s2);border:1px solid var(--border);border-radius:var(--radius);padding:16px 20px;margin-bottom:24px">
            <div style="font-size:13px;margin-bottom:4px">✅ Last scan completed</div>
            <div style="font-size:11px;color:var(--muted)">
              Added ${scanStatus.tracks_added} tracks · Skipped ${scanStatus.tracks_skipped} (already indexed)
            </div>
          </div>
        ` : ''}
      </div>

      <div class="form-group" style="border-top:1px solid var(--border);padding-top:24px">
        <label class="form-label">DSP Presets</label>
        <div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:16px">
          Apply a factory preset directly in the playback bar, or build your own custom EQ here with preamp and parametric filters.
          Factory presets are read-only; duplicate them into a custom preset before editing.
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">
          <button class="btn btn-gold" id="dsp-new-custom-btn">New Custom</button>
          <button class="btn btn-ghost" id="dsp-duplicate-active-btn">Duplicate Active Preset</button>
        </div>
        <div class="dsp-settings-grid">
          <div id="dsp-preset-grid">
            ${renderDspPresetGroups()}
          </div>

          <div id="dsp-editor-panel" style="background:#000;border:1px solid rgba(255,255,255,0.1);border-radius:20px;padding:24px;position:sticky;top:88px">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px">
              <div>
                <div style="font-size:16px;color:var(--gold);font-weight:600;font-family:'Cormorant Garamond',serif;font-style:italic">Custom DSP Editor</div>
                <div id="dsp-editor-status" style="font-size:11px;color:var(--muted);margin-top:4px">Refine your sonic signature with precision filters.</div>
              </div>
              <span id="dsp-editor-badge" style="font-size:10px;padding:4px 10px;border-radius:4px;border:1px solid rgba(255,255,255,0.1);background:rgba(255,255,255,0.03);color:var(--muted);text-transform:uppercase;letter-spacing:1px">Blank</span>
            </div>

            <div style="display:grid;gap:12px">
              <div>
                <div class="form-hint" style="margin-bottom:6px">Name</div>
                <input class="form-input" id="dsp-editor-name" type="text" placeholder="My Custom DSP">
              </div>
              <div>
                <div class="form-hint" style="margin-bottom:6px">Description</div>
                <input class="form-input" id="dsp-editor-description" type="text" placeholder="Describe the voicing">
              </div>
              <div style="display:grid;grid-template-columns:1fr 140px;gap:12px">
                <div>
                  <div class="form-hint" style="margin-bottom:6px">Category</div>
                  <select class="form-input" id="dsp-editor-category">
                    <option value="custom">Custom</option>
                    <option value="taste">Taste</option>
                    <option value="genre">Genre</option>
                    <option value="reference">Reference</option>
                  </select>
                </div>
                <div>
                  <div class="form-hint" style="margin-bottom:6px">Preamp (dB)</div>
                  <input class="form-input" id="dsp-editor-preamp" type="number" step="0.1" value="0">
                </div>
              </div>

              <div>
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px">
                  <div class="form-hint" style="margin:0">Filters</div>
                  <button class="btn btn-ghost" id="dsp-add-filter-btn" style="padding:8px 10px">Add Filter</button>
                </div>
                <div id="dsp-filter-list" style="display:grid;gap:10px"></div>
              </div>

              <div style="display:flex;gap:10px;flex-wrap:wrap">
                <button class="btn btn-gold" id="dsp-save-btn">Save Custom</button>
                <button class="btn btn-ghost" id="dsp-apply-editor-btn">Apply In Player</button>
                <button class="btn btn-ghost" id="dsp-reset-editor-btn">Reset Editor</button>
                <button class="btn btn-ghost" id="dsp-delete-editor-btn" style="color:#ff8d8d;display:none">Delete Custom</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="form-group" style="border-top:1px solid var(--border);padding-top:24px">
        <label class="form-label">Upsampling</label>
        <div style="font-size:13px;color:var(--muted);line-height:1.6">
          VELVET uses <strong style="color:var(--text)">FFmpeg + SoX resampler</strong> for high-quality PCM upsampling.<br><br>
          <strong style="color:var(--gold)">16× PCM</strong> multiplies the source sample rate (44.1 kHz → 705.6 kHz; 48 kHz → 768 kHz). This is not DSD, and browser/device support varies.
          For <em>native</em> DSD bitstream output, you need an ASIO DAC driver and software like HQPlayer.<br><br>
          Select your upsampling mode in the player bar at the bottom.
        </div>
      </div>

      <div class="form-group" style="border-top:1px solid var(--border);padding-top:24px">
        <label class="form-label">Network Access</label>
        <div style="font-size:13px;color:var(--muted);line-height:1.6">
          VELVET streams over HTTP — any device on the same network can access your library at
          <code style="color:var(--gold);font-family:'DM Mono',monospace;font-size:12px">
            http://[your-PC-IP]:8765
          </code><br><br>
          Set a password on this PC first, then connect from your phone, tablet or another authorized device. For remote access use Tailscale.
        </div>
      </div>

      <div class="form-group" style="border-top:1px solid var(--border);padding-top:24px">
        <label class="form-label">Cover Art</label>
        <div style="font-size:13px;color:var(--muted);line-height:1.6">
          VELVET automatically finds covers in this order:<br>
          1. Embedded in audio file tags<br>
          2. cover.jpg / folder.jpg in the album folder<br>
          3. MusicBrainz Cover Art Archive (fetched in background)<br><br>
          Use the "⚡ Auto Cover" button on any album page to trigger a manual lookup.<br><br>
          <button class="btn btn-ghost" id="rescan-covers-btn" style="margin-top:8px">
            🔄 Rescan Local Covers
          </button>
          <div style="font-size:11px;color:var(--dim);margin-top:8px">
            Scans all album folders for cover.jpg / folder.jpg files
          </div>
        </div>
      </div>
    </div>
  `;

  // Add fingerprint card to settings form
  const form = vc.querySelector('.settings-form');
  const fpSection = document.createElement('div');
  fpSection.className = 'form-group';
  fpSection.style.borderTop = '1px solid var(--border)';
  fpSection.style.paddingTop = '24px';
  fpSection.innerHTML = `
    <label class="form-label">Audio Fingerprinting (AcoustID)</label>
    <div class="fp-card" id="fp-card">
      <div class="fp-card-title">🎵 Automatic metadata correction</div>
      <div class="fp-card-sub">
        Uses Chromaprint audio fingerprints to identify tracks and correct wrong metadata.
        Requires <strong>fpcalc.exe</strong> in the same folder or in PATH.
        <br><small style="color:var(--dim)">Download: acoustid.org/chromaprint</small>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn btn-gold" id="btn-start-fp">Start Fingerprinting (200 tracks)</button>
        <button class="btn btn-ghost" id="btn-enrich-bios">Fetch Artist Bios (Wikipedia)</button>
      </div>
      <div id="fp-progress-section" style="margin-top:12px;display:none">
        <div class="fp-progress"><div class="fp-progress-fill" id="fp-fill" style="width:0%"></div></div>
        <div class="fp-progress-text" id="fp-text">Starting…</div>
      </div>
      <div id="bio-progress-section" style="margin-top:12px;display:none">
        <div class="fp-progress"><div class="fp-progress-fill" id="bio-fill" style="width:0%"></div></div>
        <div class="fp-progress-text" id="bio-text">Starting…</div>
      </div>
    </div>
  `;
  form?.appendChild(fpSection);

  let dspProfilesState = [...settingsDspProfiles];
  let editingDspProfileId = null;

  const parseJsonArray = (raw, fallback = []) => {
    if (!raw) return fallback;
    if (Array.isArray(raw)) return raw;
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : fallback;
    } catch {
      return fallback;
    }
  };

  function getProfileById(profileId) {
    return dspProfilesState.find(profile => String(profile.id) === String(profileId)) || null;
  }

  function syncDspPresetCardState(activeProfileId = state.dspProfile) {
    $$('.dsp-preset-card').forEach(card => {
      const isActive = String(card.dataset.profileId) === String(activeProfileId || '');
      card.style.borderColor = isActive ? 'var(--gold)' : 'var(--border)';
      card.style.boxShadow = isActive ? '0 0 0 1px rgba(201,168,76,0.18)' : 'none';
    });
  }

function applyDspProfile(profileId) {
  state.dspProfile = profileId ? String(profileId) : '';
  if (state.dspProfile) {
    state.lastEqProfile = state.dspProfile;
  }
  persistDspProfileId(state.dspProfile);
  const select = UI['dsp-select'];
  if (select) {
    select.value = state.dspProfile;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }
  syncDspPresetCardState(state.dspProfile);
}

  function getEditorFilters() {
    return Array.from($$('#dsp-filter-list .dsp-filter-row')).map(row => {
      const freq = Number(row.querySelector('.dsp-filter-freq')?.value || 1000);
      const gain = Number(row.querySelector('.dsp-filter-gain')?.value || 0);
      const q = Number(row.querySelector('.dsp-filter-q')?.value || 1.0);
      return {
        type: row.querySelector('.dsp-filter-type')?.value || 'peaking',
        freq: Number.isFinite(freq) ? freq : 1000,
        gain: Number.isFinite(gain) ? gain : 0,
        q: Number.isFinite(q) ? q : 1.0,
        enabled: !!row.querySelector('.dsp-filter-enabled')?.checked,
      };
    });
  }

  function renderFilterRows(filters = []) {
    const list = UI['dsp-filter-list'];
    if (!list) return;

    if (!filters.length) {
      list.innerHTML = `
        <div style="padding:12px;border:1px dashed var(--border);border-radius:var(--radius);font-size:12px;color:var(--muted)">
          No filters yet. Add a filter to build a custom EQ.
        </div>
      `;
      return;
    }

    const headerRow = `
      <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr 60px 80px;gap:12px;padding:0 12px;margin-bottom:8px;font-size:11px;color:rgba(255,255,255,0.4);text-transform:uppercase;letter-spacing:0.5px">
        <div>Type</div>
        <div style="text-align:center">Freq (Hz)</div>
        <div style="text-align:center">Gain (dB)</div>
        <div style="text-align:center">Q Factor</div>
        <div style="text-align:center">ON</div>
        <div style="text-align:right">Action</div>
      </div>
    `;

    list.innerHTML = headerRow + filters.map((filter, index) => `
      <div class="dsp-filter-row" style="display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr 60px 80px;gap:12px;align-items:center;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05);border-radius:12px;padding:12px">
        <select class="form-input dsp-filter-type" style="border-radius:8px;background:#111;font-size:13px">
          <option value="peaking" ${filter.type === 'peaking' ? 'selected' : ''}>Peaking</option>
          <option value="low_shelf" ${filter.type === 'low_shelf' ? 'selected' : ''}>Low Shelf</option>
          <option value="high_shelf" ${filter.type === 'high_shelf' ? 'selected' : ''}>High Shelf</option>
        </select>
        <input class="form-input dsp-filter-freq" type="number" min="20" max="20000" step="1" value="${Number(filter.freq || 1000)}" style="border-radius:8px;background:#111;text-align:center;font-size:13px">
        <input class="form-input dsp-filter-gain" type="number" min="-12" max="12" step="0.1" value="${Number(filter.gain || 0)}" style="border-radius:8px;background:#111;text-align:center;font-size:13px">
        <input class="form-input dsp-filter-q" type="number" min="0.1" max="10" step="0.1" value="${Number(filter.q || 1.0)}" style="border-radius:8px;background:#111;text-align:center;font-size:13px">
        <div style="display:flex;justify-content:center">
          <input class="dsp-filter-enabled" type="checkbox" ${filter.enabled === false ? '' : 'checked'} style="width:18px;height:18px;accent-color:var(--gold)">
        </div>
        <button class="btn btn-ghost dsp-remove-filter-btn" data-filter-index="${index}" style="padding:6px;color:#ff8d8d;font-size:12px;text-align:right">Remove</button>
      </div>
    `).join('');

    $$('.dsp-remove-filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const nextFilters = getEditorFilters();
        nextFilters.splice(Number(btn.dataset.filterIndex), 1);
        renderFilterRows(nextFilters);
      });
    });
  }

  function loadProfileIntoEditor(profile, opts = {}) {
    const isFactory = !!profile?.is_factory;
    const isBlank = !!opts.blank;
    editingDspProfileId = profile?.id || null;

    UI['dsp-editor-name'].value = profile?.name || '';
    UI['dsp-editor-description'].value = profile?.description || '';
    UI['dsp-editor-category'].value = profile?.category || 'custom';
    UI['dsp-editor-preamp'].value = String(profile?.preamp_db ?? 0);
    UI['dsp-editor-status'].textContent = isBlank
      ? 'Build a new custom profile from scratch.'
      : isFactory
        ? 'Factory presets are read-only. Save will create a custom copy.'
        : 'Editing a custom preset.';
    UI['dsp-editor-badge'].textContent = isBlank ? 'Blank' : (isFactory ? 'Factory Source' : 'Custom');
    UI['dsp-delete-editor-btn'].style.display = !isBlank && !isFactory ? 'inline-flex' : 'none';
    renderFilterRows(parseJsonArray(profile?.filter_chain_json, []));
  }

  function buildEditorPayload() {
    return {
      name: UI['dsp-editor-name'].value.trim() || 'Custom DSP',
      description: UI['dsp-editor-description'].value.trim(),
      category: UI['dsp-editor-category'].value || 'custom',
      preamp_db: Number(UI['dsp-editor-preamp'].value || 0),
      filters: getEditorFilters(),
      tags: ['custom'],
    };
  }

  async function saveEditorProfile({ applyAfterSave = false } = {}) {
    const payload = buildEditorPayload();
    const editingProfile = getProfileById(editingDspProfileId);
    const isExistingCustom = editingProfile && !editingProfile.is_factory;
    const endpoint = isExistingCustom ? `/dsp/profiles/${editingProfile.id}` : '/dsp/profiles';
    const method = isExistingCustom ? 'PUT' : 'POST';
    const res = await api(endpoint, {
      method,
      body: JSON.stringify(payload),
    });
    if (!(res?.success || res?.id)) {
      return null;
    }

    const savedProfileId = res?.id || res?.profile_id || res?.profile?.id || editingProfile?.id;
    if (!savedProfileId) {
      return null;
    }

    await refreshDspProfilesSection(savedProfileId);
    if (applyAfterSave) {
      applyDspProfile(savedProfileId);
    }

    const actionLabel = applyAfterSave ? 'applied' : 'saved';
    showToast(isExistingCustom ? `Custom preset updated and ${actionLabel}` : `Custom preset ${actionLabel}`);
    return savedProfileId;
  }

  async function refreshDspProfilesSection(nextEditorProfileId = editingDspProfileId) {
    const freshProfiles = await api('/dsp/profiles') || [];
    dspProfilesState = freshProfiles;
    state.dspProfiles = freshProfiles;
    await loadDspProfiles();
    const currentView = state.view;
    if (currentView === 'settings') {
      await renderSettings(vc);
      if (nextEditorProfileId) {
        requestAnimationFrame(() => {
          const openBtn = document.querySelector(`.dsp-open-btn[data-profile-id="${nextEditorProfileId}"]`);
          openBtn?.click();
        });
      }
    }
  }

  loadProfileIntoEditor(null, { blank: true });
  syncDspPresetCardState(activeDspId);

  UI['dsp-new-custom-btn']?.addEventListener('click', () => {
    loadProfileIntoEditor(null, { blank: true });
  });

  UI['dsp-duplicate-active-btn']?.addEventListener('click', async () => {
    const sourceId = state.dspProfile || dspProfilesState.find(profile => profile.is_factory)?.id;
    if (!sourceId) return showToast('No preset available to duplicate');
    const clone = await api(`/dsp/profiles/${sourceId}/clone`, { method: 'POST' });
    if (clone?.id) {
      showToast('Custom copy created');
      await refreshDspProfilesSection(clone.id);
    }
  });

  $$('.dsp-apply-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      applyDspProfile(btn.dataset.profileId);
    });
  });

  $$('.dsp-open-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const profile = getProfileById(btn.dataset.profileId);
      loadProfileIntoEditor(profile);
    });
  });

  $$('.dsp-clone-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const clone = await api(`/dsp/profiles/${btn.dataset.profileId}/clone`, { method: 'POST' });
      if (clone?.id) {
        showToast('Factory preset duplicated');
        await refreshDspProfilesSection(clone.id);
      }
    });
  });

  $$('.dsp-delete-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const profile = getProfileById(btn.dataset.profileId);
      if (!profile || profile.is_factory) return;
      if (!confirm(`Delete custom preset "${profile.name}"?`)) return;
      const res = await api(`/dsp/profiles/${profile.id}`, { method: 'DELETE' });
      if (res?.success) {
        if (String(state.dspProfile) === String(profile.id)) {
          applyDspProfile('');
        }
        showToast('Custom preset deleted');
        await refreshDspProfilesSection();
      }
    });
  });

  $('dsp-add-filter-btn')?.addEventListener('click', () => {
    const filters = getEditorFilters();
    filters.push({ type: 'peaking', freq: 1000, gain: 0, q: 1.0, enabled: true });
    renderFilterRows(filters);
  });

  $('dsp-reset-editor-btn')?.addEventListener('click', () => {
    loadProfileIntoEditor(null, { blank: true });
  });

  $('dsp-save-btn')?.addEventListener('click', async () => {
    await saveEditorProfile();
  });

  $('dsp-apply-editor-btn')?.addEventListener('click', async () => {
    await saveEditorProfile({ applyAfterSave: true });
  });

  $('dsp-delete-editor-btn')?.addEventListener('click', async () => {
    const editingProfile = getProfileById(editingDspProfileId);
    if (!editingProfile || editingProfile.is_factory) return;
    if (!confirm(`Delete custom preset "${editingProfile.name}"?`)) return;
    const res = await api(`/dsp/profiles/${editingProfile.id}`, { method: 'DELETE' });
    if (res?.success) {
      if (String(state.dspProfile) === String(editingProfile.id)) {
        applyDspProfile('');
      }
      showToast('Custom preset deleted');
      await refreshDspProfilesSection();
    }
  });

  $('add-lib-btn').addEventListener('click', async () => {
    const path = $('music-dir-input').value.trim();
    if (!path) return showToast('Please enter a folder path');
    const res = await api('/config/libraries', { 
        method: 'POST', 
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ path }) 
    });
    if (res.libraries) {
        renderSettings(vc);
        showToast('Library folder added');
    }
  });

  $$('.remove-lib').forEach(btn => {
    btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const res = await api(`/config/libraries/${id}`, { method: 'DELETE' });
        if (res.libraries) {
            renderSettings(vc);
            showToast('Library folder removed');
        }
    });
  });

  $$('.refresh-lib').forEach(btn => {
    btn.addEventListener('click', async () => {
        const path = btn.dataset.path;
        const btnText = btn.textContent;
        btn.textContent = '...';
        btn.disabled = true;
        try {
            // Use refresh-folder endpoint to re-read metadata from files
            const res = await api('/scan/refresh-folder', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ folder: path })
            });
            showToast(`Refreshing: ${path}`);
            startScanPolling();
        } catch (e) {
            showToast('Refresh failed: ' + e.message);
        } finally {
            btn.textContent = btnText;
            btn.disabled = false;
        }
    });
  });

  $('save-and-scan').addEventListener('click', async () => {
    await api('/scan', { method: 'POST', headers: {'Content-Type':'application/json'},
                         body: JSON.stringify({}) });
    showToast('Full scan started! This may take a while for large libraries.');
    startScanPolling();
  });

  // Reset library logic
  $('reset-library-btn')?.addEventListener('click', async () => {
    if (!confirm('!!! WARNING !!!\n\nThis will wipe ALL albums, artists, tracks, and favorites from the library database.\n\nYou will need to add folders and scan again to rebuild the index.\n\nAre you absolutely sure?')) return;
    
    showToast('Resetting library data...');
    try {
      const res = await api('/library/reset', { method: 'POST' });
      if (res.success) {
        showToast('Library wiped. Redirecting to start...', 3000);
        setTimeout(() => navigate('home'), 1500);
      } else {
        showToast('Error: ' + res.message);
      }
    } catch (e) {
      showToast('Reset failed: ' + (e.message || 'Unknown error'));
    }
  });


  // Add fingerprint card event listeners
  const fpFill = $('fp-fill');
  const fpText = $('fp-text');
  if (fpFill && fpText) {
    $('btn-start-fp')?.addEventListener('click', async () => {
      await api('/enrich/fingerprint', { method: 'POST' });
      showToast('Fingerprinting started…');
      $('fp-progress-section').style.display = 'block';
      startFpPolling();
    });

    $('btn-enrich-bios')?.addEventListener('click', async () => {
      const res = await api('/enrich/bios', { method: 'POST' });
      if (res.message === 'Already running') {
        showToast('Bio enrichment already in progress…');
      } else {
        showToast('Fetching Wikipedia bios for all artists…');
        $('bio-progress-section').style.display = 'block';
        startBioPolling();
      }
    });

    // Rescan local covers
    $('rescan-covers-btn')?.addEventListener('click', async () => {
      showToast('Scanning for local covers…');
      const res = await api('/scan/rescan-covers', { method: 'POST' });
      showToast(res.message || `Updated ${res.updated} covers`);
    });

    // Check if fingerprinting is currently running
    const fpStatus = await api('/enrich/fingerprint/status');
    if (fpStatus.running) {
      $('fp-progress-section').style.display = 'block';
      startFpPolling();
    } else if (fpStatus.matched > 0) {
      fpText.textContent = `Last run: ${fpStatus.matched} tracks corrected`;
      $('fp-progress-section').style.display = 'block';
    }

    // Check bio enrichment status
    const bioStatus = await api('/enrich/bios/status');
    if (bioStatus.running) {
      $('bio-progress-section').style.display = 'block';
      startBioPolling();
    } else if (bioStatus.fetched > 0 || bioStatus.not_found > 0) {
      const bioText = $('bio-text');
      if (bioText) bioText.textContent = `Last run: ${bioStatus.fetched || 0} fetched, ${bioStatus.not_found || 0} not found`;
      $('bio-progress-section').style.display = 'block';
    }
  }
}
