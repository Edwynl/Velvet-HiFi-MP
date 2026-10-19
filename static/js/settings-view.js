/* VELVET settings-view: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
function renderLibraryRows(libraries = []) {
  if (!libraries.length) {
    return '<div style="color:var(--muted);font-size:13px;padding:10px 0">No folders added yet.</div>';
  }
  return libraries.map(lib => `
    <div class="library-item" data-library-id="${lib.id}">
      <div class="library-path" title="${escHtml(lib.path)}">${escHtml(lib.path)}</div>
      <div class="library-item-actions">
        <button class="btn btn-gold scan-lib" data-path="${escHtml(lib.path)}" data-id="${lib.id}" title="Import new music from this folder">Scan Folder</button>
        <button class="btn btn-ghost refresh-lib" data-path="${escHtml(lib.path)}" data-id="${lib.id}" title="Re-read metadata from files already imported from this folder">Re-read Tags</button>
        <button class="btn btn-ghost remove-lib" data-id="${lib.id}" style="color:var(--muted)">Remove</button>
      </div>
    </div>
  `).join('');
}

function renderScanStatusPanel(s = {}) {
  const progress = Number(s.progress || 0);
  const total = Number(s.total || 0);
  const pct = total ? Math.min(100, Math.round((progress / total) * 100)) : 0;
  const albumsAdded = Number(s.albums_added || 0);
  const tracksAdded = Number(s.tracks_added || 0);
  const libraryAlbums = Number(s.library_albums || 0);
  const libraryTracks = Number(s.library_tracks || 0);
  const filesFailed = Number(s.files_failed || 0);
  const currentFile = escHtml(s.current_file || (s.running ? 'Discovering audio files…' : ''));
  const source = escHtml(s.music_dir || 'All configured folders');

  if (s.running) {
    return `
      <div class="scan-status-card" role="status">
        <div class="scan-status-head">
          <div>
            <div class="scan-status-title"><span class="scan-status-dot"></span>${total ? 'Importing music library' : 'Discovering music files'}</div>
            <div class="scan-status-sub" title="${source}">${source}</div>
          </div>
          <div class="scan-status-percent">${total ? `${pct}%` : '…'}</div>
        </div>
        <div class="scan-progress-bar">
          <div class="scan-progress-fill${total ? '' : ' indeterminate'}" style="width:${pct}%"></div>
        </div>
        <div class="scan-current-file" title="${currentFile}">${currentFile}</div>
        <div class="scan-status-metrics">
          <div class="scan-status-metric"><div class="scan-status-value">${albumsAdded.toLocaleString()}</div><div class="scan-status-label">New albums</div></div>
          <div class="scan-status-metric"><div class="scan-status-value">${tracksAdded.toLocaleString()}</div><div class="scan-status-label">New tracks</div></div>
          <div class="scan-status-metric"><div class="scan-status-value">${total ? `${progress.toLocaleString()} / ${total.toLocaleString()}` : progress.toLocaleString()}</div><div class="scan-status-label">Files processed</div></div>
          <div class="scan-status-metric"><div class="scan-status-value">${filesFailed.toLocaleString()}</div><div class="scan-status-label">Files failed</div></div>
        </div>
        ${s.error ? `<div class="scan-error-text">Latest issue: ${escHtml(s.error)}</div>` : ''}
      </div>
    `;
  }

  if (s.finished_at) {
    return `
      <div class="scan-status-card${s.error ? '' : ' idle'}" role="status">
        <div class="scan-status-head">
          <div>
            <div class="scan-status-title">${s.error ? '⚠ Import completed with issues' : '✓ Import complete'}</div>
            <div class="scan-status-sub">Library now contains ${libraryAlbums.toLocaleString()} albums and ${libraryTracks.toLocaleString()} tracks.</div>
          </div>
        </div>
        <div class="scan-status-metrics">
          <div class="scan-status-metric"><div class="scan-status-value">${albumsAdded.toLocaleString()}</div><div class="scan-status-label">Albums imported</div></div>
          <div class="scan-status-metric"><div class="scan-status-value">${tracksAdded.toLocaleString()}</div><div class="scan-status-label">Tracks imported</div></div>
          <div class="scan-status-metric"><div class="scan-status-value">${Number(s.tracks_skipped || 0).toLocaleString()}</div><div class="scan-status-label">Already indexed</div></div>
          <div class="scan-status-metric"><div class="scan-status-value">${filesFailed.toLocaleString()}</div><div class="scan-status-label">Files failed</div></div>
        </div>
        ${s.error ? `<div class="scan-error-text">Latest issue: ${escHtml(s.error)}</div>` : ''}
      </div>
    `;
  }

  /* Idle card. With an empty library this genuinely is "ready to import"; with a
   populated one the old wording read as if the numbers below still had to be
   imported, when they are the library that is already there. */
  const hasLibrary = libraryAlbums > 0 || libraryTracks > 0;
  return `
    <div class="scan-status-card idle">
      <div class="scan-status-title">${hasLibrary ? 'Library loaded' : 'Ready to import'}</div>
      <div class="scan-status-sub">${hasLibrary
        ? 'Everything already scanned stays put. Scan again to pick up new or changed files.'
        : 'Add as many folders as you need, then scan one folder or import all folders together.'}</div>
      <div class="scan-status-metrics is-triple">
        <div class="scan-status-metric"><div class="scan-status-value">${libraryAlbums.toLocaleString()}</div><div class="scan-status-label">Albums</div></div>
        <div class="scan-status-metric"><div class="scan-status-value">${libraryTracks.toLocaleString()}</div><div class="scan-status-label">Tracks</div></div>
        <div class="scan-status-metric"><div class="scan-status-value">${Number(s.library_artists || 0).toLocaleString()}</div><div class="scan-status-label">Artists</div></div>
      </div>
    </div>
  `;
}

function updateScanStatusPanel(status) {
  _latestScanStatus = status || {};
  const panel = $('scan-status-panel');
  if (panel) panel.innerHTML = renderScanStatusPanel(_latestScanStatus);
  setScanControlsRunning(!!_latestScanStatus.running);
}

function setScanControlsRunning(running) {
  const fullScanButton = $('save-and-scan');
  if (fullScanButton) {
    fullScanButton.disabled = running;
    fullScanButton.innerHTML = running
      ? '<span class="scan-status-dot"></span> Importing…'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg> Scan &amp; Import All';
  }
  $$('.scan-lib, .refresh-lib, .remove-lib').forEach(button => {
    button.disabled = running;
  });
}

async function renderSettings(vc) {
  const [config, scanStatus, dspProfiles] = await Promise.all([
    api('/config'), api('/scan/status'), api('/dsp/profiles')
  ]);
  const settingsDspProfiles = visibleDspProfiles(dspProfiles || []);
  const activeDspId = String(state.dspProfile || '');
  const dspCategoryOrder = ['reference', 'tone', 'genre', 'custom'];
  const renderDspOffCard = () => `
    <div class="dsp-preset-card" data-profile-id="" style="background:var(--s2);border:1px solid ${!activeDspId ? 'var(--gold)' : 'var(--border)'};border-radius:var(--radius);padding:14px 16px;margin-bottom:10px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
        <div>
          <div style="font-size:14px;color:var(--text);font-weight:600">Off</div>
          <div style="font-size:11px;color:var(--gold);text-transform:uppercase;letter-spacing:.08em;margin-top:4px">Source Direct</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
          ${!activeDspId ? '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(201,168,76,.18);color:var(--gold)">Active</span>' : ''}
        </div>
      </div>
      <div style="font-size:12px;color:var(--muted);line-height:1.55;margin-top:8px">VELVET applies no EQ or DSP. The OS mixer, browser or renderer may still process audio, so this is not a bit-perfect guarantee.</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
        ${!activeDspId
          ? '<button class="btn btn-ghost dsp-apply-btn" data-profile-id="" style="padding:8px 10px" disabled title="Already applied">Apply</button>'
          : '<button class="btn btn-gold dsp-apply-btn" data-profile-id="" style="padding:8px 10px">Apply</button>'}
      </div>
    </div>
  `;
  const renderDspPresetCard = (profile) => {
    const isActive = String(profile.id) === activeDspId;
    return `
    <div class="dsp-preset-card" data-profile-id="${profile.id}" style="background:var(--s2);border:1px solid ${isActive ? 'var(--gold)' : 'var(--border)'};border-radius:var(--radius);padding:14px 16px;margin-bottom:10px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">
        <div>
          <div style="font-size:14px;color:var(--text);font-weight:600">${escHtml(profile.name)}</div>
          <div style="font-size:11px;color:var(--gold);text-transform:uppercase;letter-spacing:.08em;margin-top:4px">${escHtml(dspProfileRoleLabel(profile))}</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
          ${isActive ? '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(201,168,76,.18);color:var(--gold)">Active</span>' : ''}
          ${profile.is_factory ? '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,.06);color:var(--muted)">Factory</span>' : '<span style="font-size:10px;padding:4px 8px;border-radius:999px;background:rgba(255,255,255,.06);color:var(--muted)">Custom</span>'}
        </div>
      </div>
      <div style="font-size:12px;color:var(--muted);line-height:1.55;margin-top:8px">${escHtml(profile.description || 'No description')}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
        <button class="btn ${isActive ? 'btn-ghost' : 'btn-gold'} dsp-apply-btn" data-profile-id="${profile.id}" style="padding:8px 10px"${isActive ? ' disabled title="Already applied"' : ''}>Apply</button>
        <button class="btn btn-ghost dsp-open-btn" data-profile-id="${profile.id}" style="padding:8px 10px">${profile.is_factory ? 'Edit Copy' : 'Edit'}</button>
        ${profile.is_factory
          ? `<button class="btn btn-ghost dsp-clone-btn" data-profile-id="${profile.id}" style="padding:8px 10px">Duplicate</button>`
          : ''}
      </div>
    </div>
  `;
  };
  const renderDspPresetGroups = () => `
      <div class="dsp-category-heading">Source Direct / VELVET DSP Off</div>
      ${renderDspOffCard()}
    ` + dspCategoryOrder
    .map(group => {
      const profiles = settingsDspProfiles.filter(profile => dspProfileGroup(profile) === group);
      if (!profiles.length) return '';
      return `
        <div class="dsp-category-heading">${dspProfileGroupLabel(group)}</div>
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
        <div class="library-section-head">
          <label class="form-label">Music Library Folders</label>
          <span class="library-count-badge" id="library-count">${config.libraries.length} folder${config.libraries.length === 1 ? '' : 's'}</span>
        </div>
        <div id="libraries-list" class="libraries-list">
          ${renderLibraryRows(config.libraries)}
        </div>

        <form class="library-add-row" id="library-add-form">
          <input class="form-input" id="music-dir-input" type="text"
                 autocomplete="off" spellcheck="false"
                 placeholder="e.g. D:\\Music">
          <button class="btn btn-gold" id="add-lib-btn" type="submit" style="white-space:nowrap">Add Folder</button>
        </form>
        <div class="library-add-feedback" id="library-add-feedback">You can add multiple folders before importing. All subfolders will be included.</div>
        <div class="form-hint">Enter a folder path on this VELVET server, such as E:\\Roon2.</div>
      </div>

      <div style="display:flex;gap:10px;margin-bottom:32px">
        <button class="btn btn-gold" id="save-and-scan">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px">
            <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>
          </svg>
          Scan &amp; Import All
        </button>
        <button class="btn btn-ghost" id="reset-library-btn" style="color:#ff6b6b;font-weight:600">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;margin-right:4px">
            <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
          </svg>
          Reset Library
        </button>
      </div>

      <div id="scan-status-panel" aria-live="polite">
        ${renderScanStatusPanel(scanStatus)}
      </div>

      <div class="form-group" style="border-top:1px solid var(--border);padding-top:24px">
        <label class="form-label">Sound / DSP Presets</label>
        <div style="font-size:13px;color:var(--muted);line-height:1.6;margin-bottom:16px">
          Choose a listening preset, or duplicate a factory preset into your own editable version.
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">
          <button class="btn btn-gold" id="dsp-new-custom-btn">New Custom Preset</button>
          <button class="btn btn-ghost" id="dsp-duplicate-active-btn">Duplicate Current</button>
        </div>
        <div class="dsp-settings-grid">
          <div id="dsp-preset-grid">
            ${renderDspPresetGroups()}
          </div>

          <div id="dsp-editor-panel" style="background:#000;border:1px solid rgba(255,255,255,0.1);border-radius:20px;padding:24px;position:sticky;top:88px">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px">
              <div>
                <div style="font-size:16px;color:var(--gold);font-weight:600;font-family:'Cormorant Garamond',serif;font-style:italic">Preset Editor</div>
                <div id="dsp-editor-status" style="font-size:11px;color:var(--muted);margin-top:4px">Create or edit a custom preset.</div>
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
                <button class="btn btn-ghost" id="dsp-save-btn">Save</button>
                <button class="btn btn-gold" id="dsp-apply-editor-btn">Save &amp; Apply</button>
                <button class="btn btn-ghost" id="dsp-reset-editor-btn">New Blank</button>
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
          Verified Tailscale devices can browse and play without a library password. Settings, cover changes and deletions require the administrator password once per browser session.
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
        <div style="padding:12px;border:1px dashed var(--border);border-radius:var(--radius);font-size:12px;color:var(--muted);line-height:1.55">
          No filters yet. This preset plays flat until you add some — saving it now is
          fine if you just want a blank to build on.
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
      ? 'Create a custom preset from scratch.'
      : isFactory
        ? 'Editing a factory preset as a new custom copy.'
        : 'Editing a custom preset.';
    UI['dsp-editor-badge'].textContent = isBlank ? 'Blank' : (isFactory ? 'Copy Source' : 'Custom');
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

  const libraryListEl = $('libraries-list');
  const addLibraryForm = $('library-add-form');
  const libraryInput = $('music-dir-input');
  const addLibraryButton = $('add-lib-btn');
  const libraryFeedback = $('library-add-feedback');

  const setLibraryFeedback = (message, type = '') => {
    if (!libraryFeedback) return;
    libraryFeedback.textContent = message;
    libraryFeedback.className = `library-add-feedback${type ? ` ${type}` : ''}`;
  };

  const updateLibraryList = libraries => {
    if (libraryListEl) libraryListEl.innerHTML = renderLibraryRows(libraries || []);
    const count = $('library-count');
    if (count) {
      const total = (libraries || []).length;
      count.textContent = `${total} folder${total === 1 ? '' : 's'}`;
    }
    setScanControlsRunning(!!_latestScanStatus?.running);
  };

  const beginLibraryScan = async folders => {
    const payload = folders?.length ? { folders } : {};
    const res = await api('/scan', { method: 'POST', body: JSON.stringify(payload) });
    if (!res) return false;
    if (res.state) updateScanStatusPanel(res.state);
    if (res.error) {
      showToast(res.message || 'Unable to start import');
      return false;
    }
    if (res.cooldown_remaining) {
      showToast(res.message);
      return false;
    }
    showToast(res.message || 'Library import started');
    startScanPolling(res.state);
    return true;
  };

  addLibraryForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const path = libraryInput?.value.trim() || '';
    if (!path) {
      setLibraryFeedback('Enter a folder path first.', 'error');
      libraryInput?.focus();
      return;
    }

    const originalLabel = addLibraryButton.textContent;
    addLibraryButton.disabled = true;
    addLibraryButton.textContent = 'Adding…';
    setLibraryFeedback(`Adding ${path}…`);

    try {
      const res = await api('/config/libraries', { method: 'POST', body: JSON.stringify({ path }) });
      const normalizePath = value => String(value || '')
        .trim()
        .replace(/\//g, '\\')
        .replace(/\\+$/, '')
        .toLocaleLowerCase();
      let libraries = Array.isArray(res?.libraries) ? res.libraries : null;

      // Confirm the saved state instead of displaying success from a stale response.
      if (!libraries?.some(lib => normalizePath(lib.path) === normalizePath(path))) {
        const freshConfig = await api('/config');
        libraries = Array.isArray(freshConfig?.libraries) ? freshConfig.libraries : libraries;
      }

      if (!libraries?.some(lib => normalizePath(lib.path) === normalizePath(path))) {
        setLibraryFeedback(`Could not save ${path}. Check the server connection and try again.`, 'error');
        return;
      }

      updateLibraryList(libraries);
      libraryInput.value = '';
      libraryInput.blur();
      if (res?.added === false) {
        setLibraryFeedback('That folder is already in the library. Enter a different path to continue.', 'error');
        showToast('Folder already added');
      } else {
        setLibraryFeedback(`${path} added. ${libraries.length} folders configured.`, 'success');
        showToast('Library folder added');
      }
    } finally {
      addLibraryButton.disabled = false;
      addLibraryButton.textContent = originalLabel;
    }
  });

  libraryInput?.addEventListener('input', () => {
    setLibraryFeedback('Ready to add this folder. Tap Add Folder to save it.');
  });

  libraryListEl?.addEventListener('click', async event => {
    const button = event.target instanceof Element ? event.target.closest('button') : null;
    if (!button || button.disabled) return;

    if (button.classList.contains('remove-lib')) {
      button.disabled = true;
      const res = await api(`/config/libraries/${button.dataset.id}`, { method: 'DELETE' });
      if (!res?.libraries) {
        button.disabled = false;
        return;
      }
      updateLibraryList(res.libraries);
      setLibraryFeedback('Folder removed. You can add a replacement path at any time.', 'success');
      showToast('Library folder removed');
      return;
    }

    const path = button.dataset.path;
    if (button.classList.contains('scan-lib')) {
      await beginLibraryScan([path]);
      return;
    }

    if (button.classList.contains('refresh-lib')) {
      button.disabled = true;
      button.textContent = 'Starting…';
      const res = await api('/scan/refresh-folder', {
        method: 'POST',
        body: JSON.stringify({ folder: path })
      });
      if (res?.state) updateScanStatusPanel(res.state);
      if (res) {
        showToast(res.message || `Refreshing: ${path}`);
        startScanPolling(res.state);
      } else {
        button.disabled = false;
        button.textContent = 'Re-read Tags';
      }
    }
  });

  $('save-and-scan')?.addEventListener('click', () => beginLibraryScan());
  updateScanStatusPanel(scanStatus);
  if (scanStatus.running) startScanPolling(scanStatus);

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
