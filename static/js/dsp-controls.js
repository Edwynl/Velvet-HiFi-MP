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
  
  let html = `<button class="eq-preset-btn ${!currentEqProfileId ? 'active' : ''}" onclick="selectEQPreset('')">Off</button>`;
  const usedLabels = new Map([['Off', 1]]);
  
  visibleDspProfiles(state.dspProfiles).forEach((profile, i) => {
    const isActive = String(currentEqProfileId || '') === String(profile.id);
    const shortLabel = uniqueDspLabel(dspDisplayLabel(profile, `DSP ${i + 1}`), usedLabels);
    html += `<button class="eq-preset-btn ${isActive ? 'active' : ''}" title="${escHtml(profile.name || shortLabel)}" onclick="selectEQPreset('${profile.id}')">${escHtml(shortLabel)}</button>`;
  });
  
  container.innerHTML = html;
}

// Mirrors dsp_engine.py: RBJ biquads as FFmpeg renders them. FFmpeg's
// bass/treble shelves behave like an RBJ shelf with Q scaled by sqrt(2).
const EQ_ANALYSIS_SAMPLE_RATE = 48000;
const EQ_HEADROOM_MARGIN_DB = 1.0;

function dspBiquadCoefficients(filter, sampleRate) {
  const isShelf = filter.type === 'low_shelf' || filter.type === 'high_shelf';
  const q = Number(filter.q) * (isShelf ? Math.SQRT2 : 1);
  const A = Math.pow(10, Number(filter.gain) / 40);
  const w0 = 2 * Math.PI * Number(filter.freq) / sampleRate;
  const cosW0 = Math.cos(w0);
  const alpha = Math.sin(w0) / (2 * q);
  const root = 2 * Math.sqrt(A) * alpha;
  if (filter.type === 'low_shelf') {
    return [A * ((A + 1) - (A - 1) * cosW0 + root), 2 * A * ((A - 1) - (A + 1) * cosW0),
      A * ((A + 1) - (A - 1) * cosW0 - root),
      (A + 1) + (A - 1) * cosW0 + root, -2 * ((A - 1) + (A + 1) * cosW0), (A + 1) + (A - 1) * cosW0 - root];
  }
  if (filter.type === 'high_shelf') {
    return [A * ((A + 1) + (A - 1) * cosW0 + root), -2 * A * ((A - 1) + (A + 1) * cosW0),
      A * ((A + 1) + (A - 1) * cosW0 - root),
      (A + 1) - (A - 1) * cosW0 + root, 2 * ((A - 1) - (A + 1) * cosW0), (A + 1) - (A - 1) * cosW0 - root];
  }
  return [1 + alpha * A, -2 * cosW0, 1 - alpha * A, 1 + alpha / A, -2 * cosW0, 1 - alpha / A];
}

// Combined magnitude (dB) of cascaded enabled filters at each frequency.
function dspFiltersResponseDb(filters, sampleRate, freqs) {
  const active = filters
    .filter(f => f.enabled !== false && Number(f.gain))
    .map(f => dspBiquadCoefficients(f, sampleRate));
  return freqs.map(freq => {
    const w = 2 * Math.PI * freq / sampleRate;
    let re = 1, im = 0;
    for (const [b0, b1, b2, a0, a1, a2] of active) {
      // H(e^jw) = (b0 + b1 e^-jw + b2 e^-2jw) / (a0 + a1 e^-jw + a2 e^-2jw)
      const nRe = b0 + b1 * Math.cos(w) + b2 * Math.cos(2 * w);
      const nIm = -b1 * Math.sin(w) - b2 * Math.sin(2 * w);
      const dRe = a0 + a1 * Math.cos(w) + a2 * Math.cos(2 * w);
      const dIm = -a1 * Math.sin(w) - a2 * Math.sin(2 * w);
      const dMag = dRe * dRe + dIm * dIm;
      const hRe = (nRe * dRe + nIm * dIm) / dMag;
      const hIm = (nIm * dRe - nRe * dIm) / dMag;
      const nextRe = re * hRe - im * hIm;
      im = re * hIm + im * hRe;
      re = nextRe;
    }
    return 20 * Math.log10(Math.max(Math.hypot(re, im), 1e-12));
  });
}

function dspPeakGainDb(filters, sampleRate = EQ_ANALYSIS_SAMPLE_RATE) {
  if (!filters.some(f => f.enabled !== false && Number(f.gain))) return 0;
  const low = 5, high = sampleRate / 2 * 0.999, points = 1024;
  const ratio = Math.pow(high / low, 1 / (points - 1));
  const grid = Array.from({ length: points }, (_, i) => low * Math.pow(ratio, i));
  filters.forEach(f => { if (Number(f.freq) < high) grid.push(Number(f.freq)); });
  return Math.max(0, ...dspFiltersResponseDb(filters, sampleRate, grid));
}

function graphicEqFilters() {
  return GEQ_BANDS
    .map((freq, i) => ({ type: 'peaking', freq, gain: Number(currentEqGains[i]) || 0, q: GEQ_DEFAULT_Q, enabled: true }))
    .filter(f => Math.abs(f.gain) > 0.05);
}

// Headroom from the combined response of all bands, not the largest slider:
// overlapping boosts add up. Never capped, so the margin is always kept.
function recommendedGraphicEqPreamp() {
  const peak = dspPeakGainDb(graphicEqFilters());
  return peak > 0.05 ? -Math.ceil((peak + EQ_HEADROOM_MARGIN_DB) * 10 - 1e-9) / 10 : 0;
}

function updateEQHeadroomNote() {
  const note = $('eq-headroom-note');
  if (!note) return;
  const peak = dspPeakGainDb(graphicEqFilters());
  const preamp = recommendedGraphicEqPreamp();
  note.textContent = preamp < 0
    ? `Auto headroom: ${preamp.toFixed(1)} dB (EQ peak +${peak.toFixed(1)} dB)`
    : 'Headroom: 0.0 dB';
}

function updateEQSaveButtonLabel() {
  const btn = UI['eq-btn-save'];
  if (!btn) return;
  const selectedProfile = state.dspProfiles?.find(p => String(p.id) === String(currentEqProfileId));
  if (!currentEqDirty && selectedProfile) {
    btn.textContent = 'Apply';
  } else if (!currentEqDirty && !currentEqProfileId) {
    btn.textContent = 'Apply Off';
  } else if (selectedProfile && !selectedProfile.is_factory) {
    btn.textContent = 'Save & Apply';
  } else {
    btn.textContent = 'Save as Custom';
  }
}

function applyEqProfileToPlayer(profileId) {
  const normalizedId = String(profileId || '');
  state.dspProfile = normalizedId;
  if (normalizedId) state.lastEqProfile = normalizedId;
  const select = $('dsp-select');
  if (select) {
    select.value = normalizedId;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  } else {
    persistDspProfileId(normalizedId);
    return false;
  }
}

window.selectEQPreset = async function(id) {
  currentEqProfileId = id;
  currentEqDirty = false;
  log('[VELVET] Selecting EQ Preset:', id);
  
  if (!id) {
    currentEqGains.fill(0);
    $('eq-current-name').textContent = 'Off';
  } else {
    const profile = state.dspProfiles.find(p => String(p.id) === String(id));
    if (profile) {
      $('eq-current-name').textContent = profile.name || 'Custom EQ';
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
  updateEQSaveButtonLabel();
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
  $('eq-current-name').textContent = 'Custom EQ';
  currentEqDirty = true;
  const selectedProfile = state.dspProfiles.find(p => String(p.id) === String(currentEqProfileId));
  if (!selectedProfile || selectedProfile.is_factory) {
    currentEqProfileId = 'custom';
  }
  renderEQPresets(); 
  updateEQCurve();
  updateEQHeadroomNote();
  updateEQSaveButtonLabel();
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
    ? visibleDspProfiles(state.dspProfiles).some(profile => String(profile.id) === String(activeProfileId))
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
    const selectableProfiles = visibleDspProfiles(profiles);
    const usedLabels = new Map([['Off', 1]]);
    const optionsByGroup = ['reference', 'tone', 'genre', 'custom']
      .map(group => {
        const groupProfiles = selectableProfiles.filter(profile => dspProfileGroup(profile) === group);
        if (!groupProfiles.length) return '';
        const options = groupProfiles.map((profile, index) => {
          const shortLabel = uniqueDspLabel(dspDisplayLabel(profile, `DSP ${index + 1}`), usedLabels);
          return `<option value="${profile.id}" title="${escHtml(profile.name || shortLabel)}" data-category="${escHtml(profile.category || 'custom')}">${escHtml(shortLabel)}</option>`;
        }).join('');
        return `<optgroup label="${escHtml(dspProfileGroupLabel(group))}">${options}</optgroup>`;
      })
      .join('');
    select.innerHTML = '<option value="">Off</option>' + optionsByGroup + '<option value="__open_eq__">Open EQ Editor</option>';

    const hasProfile = (profileId) => selectableProfiles.some(profile => String(profile.id) === String(profileId));
    if (!selectableProfiles.length) {
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
