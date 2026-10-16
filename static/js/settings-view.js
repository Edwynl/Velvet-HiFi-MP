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
