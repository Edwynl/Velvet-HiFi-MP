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

  // The rail selects the draft. Playback is reported separately by the
  // Applied/Preview status; choosing a draft must not replace the stream.
  const draftSourceId = String(eqActiveDraft().sourceId || '');
  const appliedId = String(state.dspProfile || '');
  const cls = (id) => `eq-preset-btn${String(id) === draftSourceId ? ' active' : ''}${String(id) === draftSourceId && (draftSourceId !== appliedId || eqActiveDraft().edited) ? ' is-draft' : ''}`;

  let html = `<button class="${cls('')}" title="Apply no EQ or DSP" onclick="selectEQPreset('')">Off</button>`;
  const usedLabels = new Map([['Off', 1]]);

  visibleDspProfiles(state.dspProfiles).forEach((profile, i) => {
    const shortLabel = uniqueDspLabel(dspDisplayLabel(profile, `DSP ${i + 1}`), usedLabels);
    html += `<button class="${cls(profile.id)}" title="${escHtml(profile.name || shortLabel)}" onclick="selectEQPreset('${profile.id}')">${escHtml(shortLabel)}</button>`;
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

function graphicEqFilters(gains = currentEqGains) {
  return GEQ_BANDS
    .map((freq, i) => ({ type: 'peaking', freq, gain: Number(gains[i]) || 0, q: GEQ_DEFAULT_Q, enabled: true }))
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

// Same as dsp_engine._guarded_processing at an unknown source rate: the saved
// preamp is kept while it leaves DSP_HEADROOM_GUARD_MIN_DB of headroom at the
// worst-case rate; otherwise playback applies the recommended preamp instead.
const DSP_HEADROOM_GUARD_MIN_DB = 0.5;

function dspGuardedPreampDb(peakDb, preampDb, neededDb) {
  const insufficient = peakDb + preampDb > -DSP_HEADROOM_GUARD_MIN_DB && (peakDb > 0.05 || preampDb > 0);
  return insufficient ? Math.min(preampDb, neededDb) : preampDb;
}

function dspEffectivePreampDb(filters, preampDb) {
  const active = filters.filter(f => f.enabled !== false && Number(f.gain));
  const { peakDb, preampDb: neededDb } = dspWorstCaseHeadroom(active);
  return dspGuardedPreampDb(peakDb, Number(preampDb) || 0, neededDb);
}

// Headroom from the combined response of all bands, not the largest slider:
// overlapping boosts add up. Never capped, so the margin is always kept, at
// every source rate the saved preset may be played at.
function recommendedGraphicEqPreamp() {
  return dspWorstCasePreampDb(graphicEqFilters());
}

// ─── EQ editor model ─────────────────────────────────────────────────────────
// Each A/B slot holds a draft: the preset it came from (sourceId, '' = Off)
// and the exact filters it would save. Showing a preset never rewrites its
// filters; only moving a slider turns a draft into a graphic-EQ edit, and for
// a parametric/shelf preset that edit is an explicit converted copy.
const EQ_PROFILE_EXTRAS = ['volume_normalize', 'stereo_width', 'reverb_room_size', 'reverb_wet_dry'];
const eqEditor = { slots: { A: null, B: null }, active: 'A', bypassedProfileId: null, lastPreampDb: { A: 0, B: 0 } };

function dspProfileById(id) {
  if (!id) return null;
  return (state.dspProfiles || []).find(p => String(p.id) === String(id)) || null;
}

function dspProfileFilters(profile) {
  if (!profile) return [];
  if (Array.isArray(profile.filters)) return profile.filters;
  try {
    const filters = JSON.parse(profile.filter_chain_json || '[]');
    return Array.isArray(filters) ? filters : [];
  } catch (e) {
    console.error('[VELVET] Failed to parse filters:', e);
    return [];
  }
}

// True when the filters are exactly what the 10 sliders produce, so the
// sliders can show and edit them without approximation.
function isGraphicEqChain(filters) {
  const seen = new Set();
  return filters.every(f => {
    const band = GEQ_BANDS.indexOf(Number(f.freq));
    if (f.type !== 'peaking' || band < 0 || seen.has(band) || Number(f.q) !== GEQ_DEFAULT_Q
        || f.enabled === false || !(Math.abs(Number(f.gain)) <= 12)) return false;
    seen.add(band);
    return true;
  });
}

// Slider positions for a filter chain: exact for a graphic chain; otherwise
// the 10 bands fitted to the real response at the band centres. The fit is
// only shown until a slider moves, which converts a copy.
function eqGraphicGains(filters) {
  const gains = new Array(GEQ_BANDS.length).fill(0);
  if (isGraphicEqChain(filters)) {
    filters.forEach(f => { gains[GEQ_BANDS.indexOf(Number(f.freq))] = Number(f.gain); });
    return gains;
  }
  const target = dspFiltersResponseDb(filters, EQ_ANALYSIS_SAMPLE_RATE, GEQ_BANDS);
  for (let step = 0; step < 40; step++) {
    const current = dspFiltersResponseDb(graphicEqFilters(gains), EQ_ANALYSIS_SAMPLE_RATE, GEQ_BANDS);
    gains.forEach((gain, i) => {
      gains[i] = Math.max(-12, Math.min(12, gain + 0.7 * (target[i] - current[i])));
    });
  }
  return gains.map(gain => Math.round(gain * 10) / 10);
}

function eqSourceDraft(id) {
  const profile = dspProfileById(id);
  const filters = JSON.parse(JSON.stringify(dspProfileFilters(profile)));
  const extras = {};
  EQ_PROFILE_EXTRAS.forEach(key => {
    if (profile && profile[key] != null) extras[key] = profile[key];
  });
  return {
    sourceId: profile ? String(profile.id) : '',
    name: profile ? (profile.name || 'Custom EQ') : 'Off',
    filters,
    preampDb: profile ? Number(profile.preamp_db) || 0 : 0,
    extras,
    gains: eqGraphicGains(filters),
    edited: false,
    pending: false,
    converted: false,
  };
}

function eqCloneDraft(draft) {
  return JSON.parse(JSON.stringify(draft));
}

function eqActiveDraft() {
  const slot = eqEditor.active;
  if (!eqEditor.slots[slot]) eqEditor.slots[slot] = eqSourceDraft('');
  return eqEditor.slots[slot];
}

function eqInactiveDraft() {
  return eqEditor.slots[eqEditor.active === 'A' ? 'B' : 'A'];
}

// Worst-case headroom over 12 source rates is the slow part of the editor:
// cache it per filter set. An edited draft gets the recommended preamp when
// saved; an untouched one keeps its own saved preamp.
const eqHeadroomCache = new Map();
function eqDraftHeadroom(draft, { compute = true } = {}) {
  const key = JSON.stringify([draft.filters, draft.edited ? null : draft.preampDb]);
  let result = eqHeadroomCache.get(key);
  if (!result && compute) {
    const active = draft.filters.filter(f => f.enabled !== false && Number(f.gain));
    const { peakDb, preampDb: neededDb } = dspWorstCaseHeadroom(active);
    const savedPreampDb = draft.edited ? neededDb : draft.preampDb;
    result = { peakDb, savedPreampDb, effectivePreampDb: dspGuardedPreampDb(peakDb, savedPreampDb, neededDb) };
    if (eqHeadroomCache.size > 64) eqHeadroomCache.clear();
    eqHeadroomCache.set(key, result);
  }
  return result || null;
}

function eqDraftIsApplied(draft) {
  return eqEditor.bypassedProfileId === null && !draft.edited
    && draft.sourceId === String(state.dspProfile || '');
}

// The worst-case search covers 12 sample rates; while a slider is dragged,
// recompute once input settles rather than on every tick.
let eqHeadroomNoteTimer = null;
function scheduleEQHeadroomNote() {
  clearTimeout(eqHeadroomNoteTimer);
  eqHeadroomNoteTimer = setTimeout(updateEQHeadroomNote, 150);
}

function updateEQHeadroomNote() {
  const draft = eqActiveDraft();
  const { peakDb, savedPreampDb, effectivePreampDb } = eqDraftHeadroom(draft);
  const note = $('eq-headroom-note');
  if (note) {
    const scope = `EQ peak +${peakDb.toFixed(1)} dB, worst case 22-768 kHz`;
    if (draft.edited) {
      note.textContent = effectivePreampDb < 0
        ? `Auto headroom: ${effectivePreampDb.toFixed(1)} dB (${scope})`
        : 'Headroom: 0.0 dB';
    } else if (effectivePreampDb !== savedPreampDb) {
      note.textContent = `Preamp ${savedPreampDb.toFixed(1)} dB, guard applies ${effectivePreampDb.toFixed(1)} dB (${scope})`;
    } else {
      note.textContent = peakDb > 0.05 || savedPreampDb
        ? `Preamp: ${savedPreampDb.toFixed(1)} dB (${scope})`
        : 'Headroom: 0.0 dB';
    }
  }
  updateEQCurve();
}

// ─── Frequency response plot ─────────────────────────────────────────────────
// 20 Hz-20 kHz on a log axis; dB scale grows in 6 dB steps to fit the curves.
const EQ_CURVE_W = 1000;
const EQ_CURVE_H = 300;
const EQ_CURVE_MIN_HZ = 20;
const EQ_CURVE_MAX_HZ = 20000;
const EQ_CURVE_POINTS = 256;

function eqCurveX(freq) {
  return EQ_CURVE_W * Math.log(freq / EQ_CURVE_MIN_HZ) / Math.log(EQ_CURVE_MAX_HZ / EQ_CURVE_MIN_HZ);
}

function eqCurveY(db, rangeDb) {
  const y = EQ_CURVE_H / 2 - db * (EQ_CURVE_H / 2) / rangeDb;
  return Math.max(0, Math.min(EQ_CURVE_H, y));
}

// Log grid plus each filter's centre and exact extremum frequencies, so a
// narrow peak is drawn at its true height rather than between two samples.
function eqCurveFrequencies(filters) {
  const freqs = [];
  for (let i = 0; i < EQ_CURVE_POINTS; i++) {
    freqs.push(EQ_CURVE_MIN_HZ * Math.pow(EQ_CURVE_MAX_HZ / EQ_CURVE_MIN_HZ, i / (EQ_CURVE_POINTS - 1)));
  }
  filters.forEach(f => freqs.push(Number(f.freq)));
  dspActiveCoefficients(filters, EQ_ANALYSIS_SAMPLE_RATE)
    .forEach(c => freqs.push(...dspBiquadExtremumFreqs(c, EQ_ANALYSIS_SAMPLE_RATE)));
  return [...new Set(freqs.filter(f => f >= EQ_CURVE_MIN_HZ && f <= EQ_CURVE_MAX_HZ))].sort((a, b) => a - b);
}

function eqCurvePathD(freqs, db, rangeDb) {
  return freqs.map((f, i) => `${i ? 'L' : 'M'} ${eqCurveX(f).toFixed(2)} ${eqCurveY(db[i], rangeDb).toFixed(2)}`).join(' ');
}

function eqCurveRangeDb(values) {
  const maxAbs = Math.max(0, ...values.map(v => Math.abs(v)));
  return Math.min(36, Math.max(12, Math.ceil((maxAbs + 0.5) / 6) * 6));
}

function updateEQCurve() {
  const path = $('eq-curve-path');
  const fill = $('eq-curve-fill');
  if (!path || !fill) return;
  const draft = eqActiveDraft();
  const other = eqInactiveDraft();
  // While a slider is dragged the headroom is not recomputed per tick: the
  // preamp line keeps its last position until the debounced update.
  const headroom = eqDraftHeadroom(draft, { compute: false });
  const preampDb = headroom ? headroom.effectivePreampDb : eqEditor.lastPreampDb[eqEditor.active];
  eqEditor.lastPreampDb[eqEditor.active] = preampDb;

  const freqs = eqCurveFrequencies([...draft.filters, ...(other ? other.filters : [])]);
  const db = dspFiltersResponseDb(draft.filters, EQ_ANALYSIS_SAMPLE_RATE, freqs);
  // A/B is level-matched through the preamp: the other slot is drawn at its
  // net level (EQ + preamp) relative to this slot's preamp.
  let ghostDb = null;
  if (other) {
    const otherPreampDb = eqDraftHeadroom(other).effectivePreampDb;
    ghostDb = dspFiltersResponseDb(other.filters, EQ_ANALYSIS_SAMPLE_RATE, freqs)
      .map(v => v + otherPreampDb - preampDb);
  }
  const rangeDb = eqCurveRangeDb([...db, ...(ghostDb || []), preampDb]);

  const d = eqCurvePathD(freqs, db, rangeDb);
  const zeroY = eqCurveY(0, rangeDb).toFixed(2);
  path.setAttribute('d', d);
  fill.setAttribute('d', `${d} L ${eqCurveX(freqs[freqs.length - 1]).toFixed(2)} ${zeroY} L ${eqCurveX(freqs[0]).toFixed(2)} ${zeroY} Z`);
  const svg = $('eq-curve-svg');
  if (svg) svg.dataset.rangeDb = String(rangeDb);

  const ghost = $('eq-curve-ghost');
  if (ghost) ghost.setAttribute('d', ghostDb ? eqCurvePathD(freqs, ghostDb, rangeDb) : '');

  const preampLine = $('eq-preamp-line');
  if (preampLine) {
    const y = eqCurveY(preampDb, rangeDb).toFixed(2);
    preampLine.setAttribute('y1', y);
    preampLine.setAttribute('y2', y);
    preampLine.dataset.preampDb = String(preampDb);
  }
  const preampLabel = $('eq-preamp-label');
  if (preampLabel) {
    preampLabel.textContent = `Preamp ${preampDb.toFixed(1)} dB`;
    preampLabel.style.top = `${(eqCurveY(preampDb, rangeDb) / EQ_CURVE_H) * 100}%`;
  }
  const yLabels = $('eq-curve-y-labels');
  if (yLabels && yLabels.dataset.rangeDb !== String(rangeDb)) {
    yLabels.dataset.rangeDb = String(rangeDb);
    // No label at the bottom edge: the frequency labels sit there.
    yLabels.innerHTML = [rangeDb, rangeDb / 2, 0, -rangeDb / 2]
      .map(v => `<span style="top:${(eqCurveY(v, rangeDb) / EQ_CURVE_H) * 100}%">${v > 0 ? '+' : ''}${v}dB</span>`)
      .join('');
  }
  const container = $('eq-curve-container');
  container?.classList.toggle('is-bypassed', eqEditor.bypassedProfileId !== null);
  container?.classList.toggle('has-ghost', Boolean(ghostDb));
}

// ─── Editor rendering ────────────────────────────────────────────────────────
function updateEQSaveButtonLabel() {
  const btn = UI['eq-btn-save'];
  if (!btn) return;
  const draft = eqActiveDraft();
  const source = dspProfileById(draft.sourceId);
  if (!draft.edited) {
    btn.textContent = draft.sourceId ? 'Apply' : 'Apply Off';
  } else if (source && !source.is_factory && !draft.converted) {
    btn.textContent = 'Save & Apply';
  } else {
    btn.textContent = 'Save as Custom';
  }
}

function updateEQApplyState() {
  const draft = eqActiveDraft();
  const status = $('eq-apply-state');
  if (status) {
    let phase = 'preview', label = 'Preview (not yet applied)';
    if (eqEditor.bypassedProfileId !== null) {
      phase = 'bypassed';
      label = 'Bypassed (source direct)';
    } else if (eqDraftIsApplied(draft)) {
      phase = 'applied';
      label = 'Applied';
    }
    status.dataset.state = phase;
    status.textContent = label;
  }
  const ab = $('eq-btn-ab');
  if (ab) {
    ab.textContent = `A/B: ${eqEditor.active}`;
    ab.setAttribute('aria-pressed', String(eqEditor.active === 'B'));
  }
  const bypass = $('eq-btn-bypass');
  if (bypass) bypass.setAttribute('aria-pressed', String(eqEditor.bypassedProfileId !== null));
  const duplicate = $('eq-btn-duplicate');
  if (duplicate) duplicate.disabled = !draft.sourceId && !draft.edited;
  // Export writes the saved preset, so unsaved edits are not exported.
  const exportBtn = $('eq-btn-export');
  if (exportBtn) exportBtn.disabled = !draft.sourceId || draft.edited;
}

function updateEQConversionNote() {
  const note = $('eq-conversion-note');
  if (!note) return;
  const draft = eqActiveDraft();
  const sourceName = dspProfileById(draft.sourceId)?.name || 'the preset';
  let text = '';
  if (draft.converted) {
    text = `Editing a 10-band graphic copy of "${sourceName}". The original preset is unchanged; Reset discards the copy.`;
  } else if (!draft.edited && !isGraphicEqChain(draft.filters)) {
    text = 'Parametric preset: the curve is exact, the sliders show a 10-band approximation. Moving a slider edits a graphic copy; the preset itself is never changed.';
  }
  note.textContent = text;
  note.hidden = !text;
}

function eqRenderEditor() {
  const draft = eqActiveDraft();
  currentEqGains = draft.gains;
  const nameEl = $('eq-current-name');
  if (nameEl) nameEl.textContent = draft.name;
  renderEQPresets();
  renderEQSliders();
  updateEQConversionNote();
  updateEQApplyState();
  updateEQSaveButtonLabel();
  updateEQHeadroomNote();
}

// A change from Settings also updates an open editor. Keep edited curves
// and the B comparison intact; only a clean A slot follows playback.
function syncEQEditorFromOutput() {
  if (!$('eq-overlay')?.classList.contains('active')) return;
  if (eqEditor.bypassedProfileId !== null) return;
  if (eqEditor.active === 'A' && !eqActiveDraft().edited && !eqActiveDraft().pending) {
    eqEditor.slots.A = eqSourceDraft(state.dspProfile);
  }
  eqRenderEditor();
}

function applyEqProfileToPlayer(profileId) {
  const normalizedId = String(profileId || '');
  state.dspProfile = normalizedId;
  if (normalizedId) state.lastEqProfile = normalizedId;
  const select = $('dsp-select');
  // The Settings <select> is only a mirror of the choice - the work
  // happens in one place, so the EQ editor's rail and the dropdown can
  // never drift apart.
  if (select) select.value = normalizedId;
  if (window.applyDspProfileToOutput) window.applyDspProfileToOutput(normalizedId);
  else persistDspProfileId(normalizedId);
  return true;
}

// Select first, apply with the primary button. Reloading audio on every
// preset tap interrupted listening and a failed reload erased the choice.
window.selectEQPreset = async function(id) {
  log('[VELVET] Selecting EQ Preset:', id);
  const slot = eqEditor.active;
  eqEditor.slots[slot] = eqSourceDraft(id);
  eqActiveDraft().pending = true;
  eqRenderEditor();
}

function eqGainLabelHtml(val) {
  return `${val > 0 ? '+' : ''}${val.toFixed(1)}<span class="eq-unit"> dB</span>`;
}

function renderEQSliders() {
  const wrapper = $('eq-sliders-wrapper');
  if (!wrapper) return;

  let html = '';
  GEQ_BANDS.forEach((f, i) => {
    const val = currentEqGains[i] || 0;
    const colorClass = val > 0 ? 'positive' : '';
    const label = formatFreq(f);
    const shortLabel = f >= 1000 ? `${f / 1000}k` : String(f);

    html += `
      <div class="eq-band">
        <span class="eq-band-gain ${colorClass}" id="eq-gain-val-${i}">${eqGainLabelHtml(val)}</span>
        <div class="eq-slider-track">
          <input class="eq-vertical-slider" type="range" min="-12" max="12" step="0.1" value="${val}" aria-label="${label} gain"
                 oninput="handleEQSliderInput(${i}, this.value)">
        </div>
        <span class="eq-band-freq">${shortLabel}<span class="eq-unit">Hz</span></span>
      </div>
    `;
  });
  wrapper.innerHTML = html;
}

window.handleEQSliderInput = function(i, value) {
  const draft = eqActiveDraft();
  const val = parseFloat(value);
  currentEqGains = draft.gains;
  currentEqGains[i] = val;
  const gainEl = $(`eq-gain-val-${i}`);
  if (gainEl) {
    gainEl.innerHTML = eqGainLabelHtml(val);
    gainEl.classList.toggle('positive', val > 0);
  }
  const firstEdit = !draft.edited;
  if (firstEdit) {
    const source = dspProfileById(draft.sourceId);
    draft.converted = !isGraphicEqChain(draft.filters);
    draft.edited = true;
    if (draft.converted) draft.name = `${source?.name || draft.name} (Graphic)`;
    else if (!source || source.is_factory) draft.name = 'Custom EQ';
  }
  draft.filters = graphicEqFilters(draft.gains);
  if (firstEdit) {
    const nameEl = $('eq-current-name');
    if (nameEl) nameEl.textContent = draft.name;
    updateEQConversionNote();
    updateEQApplyState();
    updateEQSaveButtonLabel();
  }
  updateEQCurve();
  scheduleEQHeadroomNote();
};

window.toggleEQAB = function() {
  if (!eqEditor.slots.B) eqEditor.slots.B = eqCloneDraft(eqActiveDraft());
  eqEditor.active = eqEditor.active === 'A' ? 'B' : 'A';
  eqRenderEditor();
};

// Bypass is a listening check on playback: Off now, the applied preset back
// on the second press (or when the editor closes).
window.toggleEQBypass = function() {
  if (eqEditor.bypassedProfileId !== null) {
    const profileId = eqEditor.bypassedProfileId;
    eqEditor.bypassedProfileId = null;
    applyEqProfileToPlayer(profileId);
  } else if (state.dspProfile) {
    eqEditor.bypassedProfileId = String(state.dspProfile);
    applyEqProfileToPlayer('');
  } else {
    showToast('No EQ applied to bypass');
  }
  updateEQApplyState();
  updateEQCurve();
};

window.resetEQDraft = function() {
  eqEditor.slots[eqEditor.active] = eqSourceDraft(eqActiveDraft().sourceId);
  eqRenderEditor();
};

// Saves a draft's exact filters: an untouched preset keeps its type, freq,
// gain, Q, enabled and order, its saved preamp and its other effects.
async function eqPersistDraft(draft, { target = null, name = draft.name } = {}) {
  const profile = {
    ...draft.extras,
    name,
    description: target?.description || (draft.sourceId
      ? `Saved from the EQ Editor, based on ${dspProfileById(draft.sourceId)?.name || 'a preset'}.`
      : 'Saved from the EQ Editor.'),
    category: 'custom',
    preamp_db: eqDraftHeadroom(draft).savedPreampDb,
    filters: draft.filters,
    tags: isGraphicEqChain(draft.filters) ? ['custom', 'graphic-eq'] : ['custom'],
  };
  const endpoint = target ? `/dsp/profiles/${target.id}` : '/dsp/profiles';
  const res = await api(endpoint, { method: target ? 'PUT' : 'POST', body: JSON.stringify(profile) });
  if (!res || !(res.success || res.id)) return '';
  const savedId = String(res.id || res.profile_id || res.profile?.id || target?.id || '');
  await loadDspProfiles();
  return savedId;
}

function eqApplyAndClose(profileId) {
  eqEditor.bypassedProfileId = null;
  applyEqProfileToPlayer(profileId);
  UI['eq-overlay']?.classList.remove('active');
}

// After a save the slot shows the stored preset, unless it was switched to
// another preset while the request was in flight.
function eqReplaceSavedDraft(draft, savedId) {
  ['A', 'B'].forEach(slot => {
    if (eqEditor.slots[slot] === draft) eqEditor.slots[slot] = eqSourceDraft(savedId);
  });
}

window.saveEQDraft = async function() {
  const btn = UI['eq-btn-save'];
  const draft = eqActiveDraft();
  if (!draft.edited) {
    // Untouched preset: applied as stored, nothing is written.
    if (!eqDraftIsApplied(draft)) {
      eqEditor.bypassedProfileId = null;
      draft.pending = false;
      applyEqProfileToPlayer(draft.sourceId);
    } else {
      // Already in force: say so instead of closing the panel silently.
      showToast(draft.sourceId ? `DSP: ${draft.name}` : 'DSP: Off');
    }
    UI['eq-overlay']?.classList.remove('active');
    return;
  }
  const source = dspProfileById(draft.sourceId);
  const overwrite = source && !source.is_factory && !draft.converted ? source : null;
  if (btn) btn.textContent = 'Saving...';
  try {
    // api() already offers the administrator unlock and repeats the write,
    // so a refusal here means the unlock did not happen. Say so instead of
    // failing quietly - the phone has no admin session most of the time.
    const savedId = await eqPersistDraft(draft, { target: overwrite });
    if (savedId) {
      eqReplaceSavedDraft(draft, savedId);
      eqApplyAndClose(savedId);
      showToast('Preset saved and applied');
    } else {
      showToast('Preset not saved. Writing one needs the administrator password.', 6000);
    }
  } catch (err) {
    showToast('Failed to save EQ');
  }
  eqRenderEditor();
};

async function eqSaveCopy(name) {
  const draft = eqActiveDraft();
  try {
    let savedId = '';
    if (!draft.edited && draft.sourceId) {
      // Server-side copy: every stored field of the preset, unchanged.
      const res = await api(`/dsp/profiles/${draft.sourceId}/clone`, { method: 'POST', body: JSON.stringify({ name }) });
      savedId = res?.id ? String(res.id) : '';
      if (savedId) await loadDspProfiles();
    } else {
      savedId = await eqPersistDraft(draft, { name });
    }
    if (!savedId) {
      // api() already offered the administrator unlock; a refusal here
      // means it did not happen.
      showToast('Preset not saved. Writing one needs the administrator password.', 6000);
      return;
    }
    eqReplaceSavedDraft(draft, savedId);
    eqRenderEditor();
    showToast(`Saved "${name}" (not yet applied)`);
  } catch (err) {
    showToast('Failed to save EQ copy');
  }
}

window.duplicateEQDraft = function() {
  const draft = eqActiveDraft();
  if (!draft.sourceId && !draft.edited) return Promise.resolve();
  return eqSaveCopy(`${draft.name} Copy`);
};

window.saveEQDraftAs = function() {
  const draft = eqActiveDraft();
  const name = String(window.prompt('Save EQ preset as', `${draft.name} Copy`) || '').trim();
  return name ? eqSaveCopy(name) : Promise.resolve();
};

// ─── AutoEq import / export ──────────────────────────────────────────────────
// The server parses ParametricEQ.txt strictly (dsp_engine.parse_autoeq_parametric_eq)
// and maps AutoEq's shelf Q exactly; the editor only collects the text.
function setAutoEqImportError(message) {
  const error = $('eq-import-error');
  if (!error) return;
  error.textContent = message || '';
  error.hidden = !message;
}

window.toggleAutoEqImport = function(open) {
  const panel = $('eq-import-panel');
  if (!panel) return;
  const show = typeof open === 'boolean' ? open : panel.hidden;
  panel.hidden = !show;
  $('eq-btn-import')?.setAttribute('aria-expanded', String(show));
  if (show) {
    setAutoEqImportError('');
    $('eq-import-text')?.focus();
  }
};

// "Sennheiser HD 600 ParametricEQ.txt" -> "Sennheiser HD 600 (AutoEq)"
function autoEqNameFromFile(fileName) {
  const base = String(fileName || '').replace(/\.txt$/i, '').replace(/\s*ParametricEQ$/i, '').trim();
  return base ? `${base} (AutoEq)`.slice(0, 120) : '';
}

window.loadAutoEqFile = async function(file) {
  if (!file) return;
  const text = await file.text();
  const textArea = $('eq-import-text');
  if (textArea) textArea.value = text;
  const nameInput = $('eq-import-name');
  if (nameInput && !nameInput.value.trim()) nameInput.value = autoEqNameFromFile(file.name);
  setAutoEqImportError('');
};

async function autoEqRequest(path, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (authState.apiKey) headers['X-API-Key'] = authState.apiKey;
  const res = await fetch('/api' + path, { method: 'POST', headers, body: JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch {}
  if (res.status === 401) {
    showLoginModal(data?.error);
    throw new Error('Sign in to import presets.');
  }
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : '';
    throw new Error(detail.replace(/^Invalid (AutoEq|DSP) profile: /, '') || `Import failed (${res.status})`);
  }
  return data;
}

window.submitAutoEqImport = async function() {
  const text = $('eq-import-text')?.value || '';
  const name = ($('eq-import-name')?.value || '').trim();
  if (!text.trim()) return setAutoEqImportError('Paste the ParametricEQ.txt contents or choose the file.');
  if (!name) return setAutoEqImportError('Enter a name for the preset.');
  const submit = $('eq-import-submit');
  if (submit) submit.disabled = true;
  try {
    const res = await autoEqRequest('/dsp/profiles/import/autoeq', { name, text });
    await loadDspProfiles();
    eqEditor.slots[eqEditor.active] = eqSourceDraft(String(res.id));
    window.toggleAutoEqImport(false);
    $('eq-import-text').value = '';
    $('eq-import-name').value = '';
    $('eq-import-file').value = '';
    eqRenderEditor();
    showToast(`Imported "${name}" (not yet applied)`);
  } catch (err) {
    setAutoEqImportError(err.message);
  } finally {
    if (submit) submit.disabled = false;
  }
};

window.exportAutoEq = async function() {
  const draft = eqActiveDraft();
  if (!draft.sourceId || draft.edited) return;
  const res = await api(`/dsp/profiles/${draft.sourceId}/autoeq`);
  if (!res?.text) {
    showToast('Export failed');
    return;
  }
  const url = URL.createObjectURL(new Blob([res.text], { type: 'text/plain' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = res.filename.replace(/[\\/:*?"<>|]/g, '_');
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

function eqHasUnsavedWork() {
  return Object.values(eqEditor.slots).some(slot => slot && slot.edited);
}

function eqDiscardDrafts() {
  eqEditor.slots.A = null;
  eqEditor.slots.B = null;
}

/* Closing the editor used to drop an edited draft without a word, which reads
   as "the EQ ignored me". Ask before throwing work away. */
window.closeGraphicEQ = function() {
  const shutdown = () => {
    eqDiscardDrafts();
    if (eqEditor.bypassedProfileId !== null) window.toggleEQBypass();
    UI['eq-overlay']?.classList.remove('active');
  };
  if (!eqHasUnsavedWork()) { shutdown(); return; }
  showConfirm({
    title: 'Discard EQ changes?',
    body: 'This preset has unsaved filter changes. Closing now throws them away and leaves playback as it is.',
    confirmText: 'Discard',
    onConfirm: shutdown,
  });
};

window.openGraphicEQ = async function() {
  $('eq-overlay').classList.add('active');
  if (!state.dspProfiles?.length) {
    await loadDspProfiles();
  }
  const activeProfileId = state.dspProfile || '';
  const hasProfile = activeProfileId
    ? visibleDspProfiles(state.dspProfiles).some(profile => String(profile.id) === String(activeProfileId))
    : false;
  eqEditor.slots = { A: eqSourceDraft(hasProfile ? String(activeProfileId) : ''), B: null };
  eqEditor.active = 'A';
  eqEditor.bypassedProfileId = null;
  eqRenderEditor();
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
