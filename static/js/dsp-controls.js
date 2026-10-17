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
// Mirrors dsp_engine.DSP_REFERENCE_SAMPLE_RATES: the EQ runs at the source
// rate, and peaks near the top of the band grow with it (+15.2 dB at 48 kHz
// vs +17.0 dB at 192 kHz for the same curve), so a saved preamp must cover all.
const DSP_REFERENCE_SAMPLE_RATES = [22050, 32000, 44100, 48000, 88200, 96000,
  176400, 192000, 352800, 384000, 705600, 768000];

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

function dspActiveCoefficients(filters, sampleRate) {
  return filters
    .filter(f => f.enabled !== false && Number(f.gain))
    .map(f => dspBiquadCoefficients(f, sampleRate));
}

// H(e^jw) = (b0 + b1 e^-jw + b2 e^-2jw) / (a0 + a1 e^-jw + a2 e^-2jw), cascaded.
function dspCascadeDb(coefficients, sampleRate, freq) {
  const w = 2 * Math.PI * freq / sampleRate;
  const cos1 = Math.cos(w), sin1 = Math.sin(w), cos2 = Math.cos(2 * w), sin2 = Math.sin(2 * w);
  let re = 1, im = 0;
  for (const [b0, b1, b2, a0, a1, a2] of coefficients) {
    const nRe = b0 + b1 * cos1 + b2 * cos2;
    const nIm = -b1 * sin1 - b2 * sin2;
    const dRe = a0 + a1 * cos1 + a2 * cos2;
    const dIm = -a1 * sin1 - a2 * sin2;
    const dMag = dRe * dRe + dIm * dIm;
    const hRe = (nRe * dRe + nIm * dIm) / dMag;
    const hIm = (nIm * dRe - nRe * dIm) / dMag;
    const nextRe = re * hRe - im * hIm;
    im = re * hIm + im * hRe;
    re = nextRe;
  }
  return 20 * Math.log10(Math.max(Math.hypot(re, im), 1e-12));
}

// Same as dsp_engine._biquad_extremum_freqs: |H|^2 = N(c)/D(c), c = cos(w),
// so a biquad's stationary points solve a quadratic in c (exact, closed form).
function dspBiquadExtremumFreqs([b0, b1, b2, a0, a1, a2], sampleRate) {
  const powerPoly = (x0, x1, x2) => [x0 * x0 + x1 * x1 + x2 * x2 - 2 * x0 * x2, 2 * (x0 * x1 + x1 * x2), 4 * x0 * x2];
  const [n0, n1, n2] = powerPoly(b0, b1, b2);
  const [d0, d1, d2] = powerPoly(a0, a1, a2);
  const qa = n2 * d1 - n1 * d2, qb = 2 * (n2 * d0 - n0 * d2), qc = n1 * d0 - n0 * d1;
  const disc = qb * qb - 4 * qa * qc;
  if (disc < 0) return [];
  // Numerically stable roots: no cancellation between -qb and sqrt(disc).
  const q = -0.5 * (qb + (qb < 0 ? -1 : 1) * Math.sqrt(disc));
  const roots = [qa ? q / qa : null, q ? qc / q : null].filter(c => c !== null);
  return roots.filter(c => c > -1 && c < 1).map(c => Math.acos(c) * sampleRate / (2 * Math.PI));
}

// Combined magnitude (dB) of cascaded enabled filters at each frequency.
function dspFiltersResponseDb(filters, sampleRate, freqs) {
  const coefficients = dspActiveCoefficients(filters, sampleRate);
  return freqs.map(freq => dspCascadeDb(coefficients, sampleRate, freq));
}

// Same search as dsp_engine.peak_gain_db / _peak_search_grid: a log grid
// from 1 Hz, a mirrored grid log-spaced in the distance to Nyquist (warping
// squeezes resonances there), filter centres and each biquad's exact extremum
// frequencies; local maxima near the grid maximum are refined by
// golden-section search; DC/Nyquist are exact.
const DSP_PEAK_GRID_POINTS = 2048;
const DSP_PEAK_GRID_MIN_DISTANCE_HZ = 0.01;
const DSP_PEAK_REFINE_WINDOW_DB = 1.0;

function dspPeakSearchGrid(filters, coefficients, sampleRate) {
  const nyquist = sampleRate / 2;
  const points = [];
  for (let i = 0; i < DSP_PEAK_GRID_POINTS; i++) {
    points.push(Math.exp(Math.log(nyquist) * i / (DSP_PEAK_GRID_POINTS - 1)));
  }
  const half = DSP_PEAK_GRID_POINTS / 2;
  const span = Math.log(nyquist / DSP_PEAK_GRID_MIN_DISTANCE_HZ);
  for (let i = 0; i < half; i++) {
    points.push(nyquist - DSP_PEAK_GRID_MIN_DISTANCE_HZ * Math.exp(span * i / (half - 1)));
  }
  filters.forEach(f => points.push(Number(f.freq)));
  coefficients.forEach(c => points.push(...dspBiquadExtremumFreqs(c, sampleRate)));
  return [...new Set(points.filter(f => f > 0 && f < nyquist))].sort((a, b) => a - b);
}

function dspPeakGainDb(filters, sampleRate = EQ_ANALYSIS_SAMPLE_RATE) {
  const coefficients = dspActiveCoefficients(filters, sampleRate);
  if (!coefficients.length) return 0;
  const nyquist = sampleRate / 2;
  const response = freq => dspCascadeDb(coefficients, sampleRate, freq);
  const grid = dspPeakSearchGrid(filters, coefficients, sampleRate);
  const values = grid.map(response);
  const gridMax = Math.max(...values);
  let best = Math.max(gridMax, response(0), response(nyquist));
  const golden = (Math.sqrt(5) - 1) / 2;
  values.forEach((value, i) => {
    if (value < gridMax - DSP_PEAK_REFINE_WINDOW_DB) return;
    if (value < (values[i - 1] ?? -Infinity) || value < (values[i + 1] ?? -Infinity)) return;
    let a = grid[i - 1] ?? 0, b = grid[i + 1] ?? nyquist;
    let c = b - golden * (b - a), d = a + golden * (b - a);
    let fc = response(c), fd = response(d);
    for (let step = 0; step < 40; step++) {
      if (fc > fd) {
        b = d; d = c; fd = fc; c = b - golden * (b - a); fc = response(c);
      } else {
        a = c; c = d; fc = fd; d = a + golden * (b - a); fd = response(d);
      }
    }
    best = Math.max(best, fc, fd);
  });
  return Math.max(0, best);
}

function graphicEqFilters() {
  return GEQ_BANDS
    .map((freq, i) => ({ type: 'peaking', freq, gain: Number(currentEqGains[i]) || 0, q: GEQ_DEFAULT_Q, enabled: true }))
    .filter(f => Math.abs(f.gain) > 0.05);
}

function dspPreampForPeakDb(peak) {
  return peak > 0.05 ? -Math.ceil((peak + EQ_HEADROOM_MARGIN_DB) * 10 - 1e-9) / 10 : 0;
}

// Same as dsp_engine.worst_case_preamp_db: filters at/above a rate's Nyquist
// are disabled there, so they are left out for that rate.
function dspWorstCaseHeadroom(filters) {
  let peakDb = 0;
  DSP_REFERENCE_SAMPLE_RATES.forEach(rate => {
    peakDb = Math.max(peakDb, dspPeakGainDb(filters.filter(f => Number(f.freq) < rate / 2), rate));
  });
  return { peakDb, preampDb: dspPreampForPeakDb(peakDb) };
}

function dspWorstCasePreampDb(filters) {
  return dspWorstCaseHeadroom(filters).preampDb;
}

// Headroom from the combined response of all bands, not the largest slider:
// overlapping boosts add up. Never capped, so the margin is always kept, at
// every source rate the saved preset may be played at.
function recommendedGraphicEqPreamp() {
  return dspWorstCasePreampDb(graphicEqFilters());
}

// The worst-case search covers 12 sample rates; while a slider is dragged,
// recompute once input settles rather than on every tick.
let eqHeadroomNoteTimer = null;
function scheduleEQHeadroomNote() {
  clearTimeout(eqHeadroomNoteTimer);
  eqHeadroomNoteTimer = setTimeout(updateEQHeadroomNote, 150);
}

function updateEQHeadroomNote() {
  const note = $('eq-headroom-note');
  if (!note) return;
  const { peakDb, preampDb } = dspWorstCaseHeadroom(graphicEqFilters());
  note.textContent = preampDb < 0
    ? `Auto headroom: ${preampDb.toFixed(1)} dB (EQ peak +${peakDb.toFixed(1)} dB, worst case 22-768 kHz)`
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
  scheduleEQHeadroomNote();
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
    const preferredProfileId = state.dspProfile
      || (isDspExplicitlyOff() ? '' : (state.lastEqProfile || persistedLastProfileId));
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
