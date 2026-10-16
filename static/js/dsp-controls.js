/* VELVET dsp-controls: extracted from the original classic-script application.
 * Loaded before inline bootstrap; global function declarations preserve existing handlers.
 * Keep the module classic (not type=module) until the application state is migrated.
 */
function formatFreq(f) {
  return f >= 1000 ? (f/1000) + 'kHz' : f + 'Hz';
}

function renderEQPresets() {
  const container = $('eq-presets-container');
  if (!container || !state.dspProfiles) return;
  
  let html = `<button class="eq-preset-btn ${!currentEqProfileId ? 'active' : ''}" onclick="selectEQPreset('')">Dry (Flat)</button>`;
  const usedLabels = new Map([['Dry (Flat)', 1]]);
  
  state.dspProfiles.forEach((profile, i) => {
    const isActive = String(currentEqProfileId || '') === String(profile.id);
    const shortLabel = uniqueDspLabel(dspDisplayLabel(profile, `DSP ${i + 1}`), usedLabels);
    html += `<button class="eq-preset-btn ${isActive ? 'active' : ''}" title="${escHtml(profile.name || shortLabel)}" onclick="selectEQPreset('${profile.id}')">${escHtml(shortLabel)}</button>`;
  });
  
  container.innerHTML = html;
}

function recommendedGraphicEqPreamp() {
  const maxBoost = Math.max(0, ...currentEqGains.map(gain => Number(gain) || 0));
  return maxBoost > 0.05 ? -Math.min(12, Math.ceil((maxBoost + 1) * 10) / 10) : 0;
}

function updateEQHeadroomNote() {
  const note = $('eq-headroom-note');
  if (!note) return;
  const preamp = recommendedGraphicEqPreamp();
  note.textContent = preamp < 0 ? `Auto headroom: ${preamp.toFixed(1)} dB` : 'Headroom: 0.0 dB';
}

window.selectEQPreset = async function(id) {
  currentEqProfileId = id;
  log('[VELVET] Selecting EQ Preset:', id);
  
  if (!id) {
    currentEqGains.fill(0);
    $('eq-current-name').textContent = 'Dry (Flat)';
  } else {
    const profile = state.dspProfiles.find(p => String(p.id) === String(id));
    if (profile) {
      $('eq-current-name').textContent = profile.name || 'Custom Preset';
      currentEqGains.fill(0);
      
      let filters = profile.filters;
      if (!filters && profile.filter_chain_json) {
        try {
          filters = JSON.parse(profile.filter_chain_json);
        } catch (e) {
          console.error('[VELVET] Failed to parse filters:', e);
        }
      }

      if (filters && Array.isArray(filters)) {
        filters.forEach(f => {
          // Both 'peaking' and 'shelf' filters contribute to the perceived curve
          if (f.type !== 'peaking' && !f.type.includes('shelf')) return;
          
          let minDiff = 999999;
          let bestIdx = -1;
          GEQ_BANDS.forEach((bf, idx) => {
            const diff = Math.abs(bf - f.freq);
            if(diff < minDiff) { 
              minDiff = diff; 
              bestIdx = idx; 
            }
          });
          
          // Map to the closest band if it's within a reasonable octave range
          // Graphic EQ is an approximation of the underlying DSP
          if (bestIdx !== -1) {
            const gain = parseFloat(f.gain) || 0;
            // If multiple filters map to the same band, we sum them (simple approximation)
            currentEqGains[bestIdx] += gain;
          }
        });
        
        // Clamp gains to +/- 12dB
        for(let i=0; i<currentEqGains.length; i++) {
          currentEqGains[i] = Math.max(-12, Math.min(12, currentEqGains[i]));
        }
      }
    }
  }
  
  renderEQPresets();
  renderEQSliders();
  updateEQCurve();
  updateEQHeadroomNote();
}

function renderEQSliders() {
  const wrapper = $('eq-sliders-wrapper');
  if (!wrapper) return;
  
  let html = '';
  GEQ_BANDS.forEach((f, i) => {
    const val = currentEqGains[i] || 0;
    const sign = val > 0 ? '+' : '';
    const colorClass = val > 0 ? 'positive' : '';
    const label = formatFreq(f);
    
    html += `
      <div class="eq-band">
        <span class="eq-band-gain ${colorClass}" id="eq-gain-val-${i}">${sign}${val.toFixed(1)} dB</span>
        <div class="eq-slider-track">
          <input class="eq-vertical-slider" type="range" min="-12" max="12" step="0.1" value="${val}" 
                 oninput="handleEQSliderInput(${i}, this.value)">
        </div>
        <span class="eq-band-freq">${label}</span>
      </div>
    `;
  });
  wrapper.innerHTML = html;
}

window.handleEQSliderInput = function(i, value) {
  const val = parseFloat(value);
  currentEqGains[i] = val;
  const gainEl = $(`eq-gain-val-${i}`);
  if (gainEl) {
    const sign = val > 0 ? '+' : '';
    gainEl.textContent = `${sign}${val.toFixed(1)} dB`;
    if (val > 0) gainEl.classList.add('positive');
    else gainEl.classList.remove('positive');
  }
  $('eq-current-name').textContent = 'Custom Profile';
  currentEqProfileId = 'custom';
  renderEQPresets(); 
  updateEQCurve();
  updateEQHeadroomNote();
};

function updateEQCurve() {
  const path = $('eq-curve-path');
  const fill = $('eq-curve-fill');
  if (!path || !fill) return;
  
  const points = [];
  const W = 1000;
  const H = 300;
  const midY = 150;
  const pixelsPerDb = 10; 
  
  for (let x = 0; x <= W; x += 5) {
    const freq = 20 * Math.pow(2, (x / W) * 9.965784);
    
    let totalDb = 0;
    for (let i=0; i<10; i++) {
      const g = currentEqGains[i];
      if (Math.abs(g) < 0.1) continue;
      const ratio = freq / GEQ_BANDS[i];
      const invRatio = GEQ_BANDS[i] / freq;
      const qTerm = GEQ_DEFAULT_Q * (ratio - invRatio);
      const mag = g / (1 + qTerm * qTerm);
      totalDb += mag;
    }
    
    const y = midY - (totalDb * pixelsPerDb);
    points.push(`${x},${y}`);
  }
  
  const d = `M 0 150 L ${points.map(p => p.replace(',', ' ')).join(' L ')} L 1000 150`;
  path.setAttribute('d', d);
  fill.setAttribute('d', d + ' V 300 H 0 Z');
}

window.openGraphicEQ = async function() {
  $('eq-overlay').classList.add('active');
  if (!state.dspProfiles?.length) {
    await loadDspProfiles();
  }
  const activeProfileId = state.dspProfile || state.lastEqProfile || '';
  const hasProfile = activeProfileId
    ? state.dspProfiles.some(profile => String(profile.id) === String(activeProfileId))
    : false;
  currentEqProfileId = hasProfile ? String(activeProfileId) : '';
  selectEQPreset(currentEqProfileId);
  updateEQHeadroomNote();
}



async function loadDspProfiles() {
  try {
    const select = $('dsp-select');
    if (!select) return;

    const profiles = await api('/dsp/profiles');
    if (!profiles) return;

    state.dspProfiles = profiles;
    const usedLabels = new Map([['Dry', 1]]);
    select.innerHTML = '<option value="">Dry</option>' + profiles.map((profile, index) => {
      const shortLabel = uniqueDspLabel(dspDisplayLabel(profile, `DSP ${index + 1}`), usedLabels);
      return `<option value="${profile.id}" title="${escHtml(profile.name || shortLabel)}" data-category="${escHtml(profile.category || 'custom')}">${escHtml(shortLabel)}</option>`;
    }).join('');

    const hasProfile = (profileId) => profiles.some(profile => String(profile.id) === String(profileId));
    if (!profiles.length) {
      select.value = '';
      syncMobileLandscapeControls();
      return;
    }
    const persistedLastProfileId = readPersistedLastDspProfileId();
    const preferredProfileId = state.dspProfile || state.lastEqProfile || persistedLastProfileId;
    if (preferredProfileId && hasProfile(preferredProfileId)) {
      state.dspProfile = String(preferredProfileId);
      state.lastEqProfile = state.dspProfile;
      select.value = state.dspProfile;
    } else {
      state.dspProfile = '';
      select.value = '';
    }
    persistDspProfileId(state.dspProfile);
    syncMobileLandscapeControls();
  } catch (e) {
    console.warn('[VELVET] Failed to load DSP profiles:', e);
  }
}
