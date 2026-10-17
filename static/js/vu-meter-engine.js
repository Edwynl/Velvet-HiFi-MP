/* VELVET analogue-style VU display engine.
 * The IEC 60268-17 reference describes an electromechanical meter. This
 * sampled-browser approximation is NOT a certified IEC measurement device.
 * Meter movement is driven by the time-domain full-wave rectified mean,
 * not FFT magnitude, peak hold, or perceived loudness/LUFS.
 *
 * A sine wave at the chosen dBFS RMS alignment reads 0 VU. -18 dBFS is
 * a typical studio alignment, not an intrinsic digital-VU standard.
 * The visual-only Responsive mode deliberately changes the scale.
 */
(function (root) {
  'use strict';

  const MIN_DB = -20;
  const MAX_DB = 3;
  const MIN_ANGLE = -60;
  const MAX_ANGLE = 60;
  const SCALE_MIN = Math.pow(10, MIN_DB / 20);
  const SCALE_MAX = Math.pow(10, MAX_DB / 20);
  // E[|sin|] = 2/pi; RMS(sin) = 1/sqrt(2).
  const RECTIFIED_MEAN_TO_SINE_RMS = Math.PI / (2 * Math.SQRT2);

  // 2nd-order moving-coil approximation: 99% rise at 300 ms, ~1.11% overshoot
  // for an ideal step in linear needle-drive amplitude. Integrate EXACTLY
  // over each elapsed timestep, independent of requestAnimationFrame rate.
  const DAMPING = 0.82;
  const NATURAL_FREQUENCY = 13.758752060525728; // rad/s
  const DAMPED_FREQUENCY = NATURAL_FREQUENCY * Math.sqrt(1 - DAMPING * DAMPING);
  const DECAY = DAMPING * NATURAL_FREQUENCY;

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function pcmLevel(samples) {
    if (!samples || !samples.length) return { sineRms: 0, rms: 0, peak: 0 };
    let absSum = 0;
    let squareSum = 0;
    let peak = 0;
    let count = 0;
    for (let i = 0; i < samples.length; i++) {
      const value = samples[i];
      if (!Number.isFinite(value)) continue;
      const abs = Math.abs(value);
      absSum += abs;
      squareSum += value * value;
      peak = Math.max(peak, abs);
      count++;
    }
    if (!count) return { sineRms: 0, rms: 0, peak: 0 };
    return {
      sineRms: absSum / count * RECTIFIED_MEAN_TO_SINE_RMS,
      rms: Math.sqrt(squareSum / count),
      peak,
    };
  }

  // Analyser window covering >= ~42 ms (longer than a 30 fps frame) at the
  // AudioContext's own rate: 2048 at 44.1/48 kHz, 8192 at 192 kHz.
  function analyserFftSize(sampleRate) {
    const rate = Number.isFinite(sampleRate) && sampleRate > 0 ? sampleRate : 48000;
    return clamp(Math.pow(2, Math.ceil(Math.log2(rate * 0.042))), 2048, 32768);
  }

  // The VU mirror never requests an upsampled or DSP stream: each of those
  // holds one of the server's few FFmpeg slots and could leave the MAIN
  // player with a 503. Upsampling does not change levels; with DSP active the
  // meter reads the source (pre-DSP) signal.
  function mirrorSourceUrl(src, base) {
    if (!src) return '';
    let url;
    try { url = new URL(src, base); } catch { return src; }
    if (!/^\/api\/stream\/\d+$/.test(url.pathname)) return url.href;
    url.searchParams.set('upsample', 'none');
    url.searchParams.delete('dsp');
    url.searchParams.delete('quality');
    return url.href;
  }

  // Retry policy for the mirror's play(). It starts armed, so the first
  // request (inside the VU-open gesture) is never throttled however early
  // after page load performance.now() reads.
  function createPlayGate({ retryMs = 1200, failureMs = 5000, deniedMs = 30000 } = {}) {
    let nextAt = -Infinity;
    let pending = false;
    let needsGesture = false;
    return {
      canAttempt: now => !pending && now >= nextAt,
      begin(now) { pending = true; nextAt = now + retryMs; },
      settle(error, now) {
        pending = false;
        if (!error) { needsGesture = false; return; }
        if (error.name === 'NotAllowedError') {
          needsGesture = true;
          nextAt = now + deniedMs;
        } else if (error.name !== 'AbortError') {
          nextAt = now + failureMs; // e.g. 401/404/undecodable: no request storm
        }
      },
      rearm() { nextAt = -Infinity; needsGesture = false; },
      get needsGesture() { return needsGesture; },
      get pending() { return pending; },
    };
  }

  function vuToPosition(vu) {
    if (!Number.isFinite(vu)) return 0;
    const amplitude = Math.pow(10, clamp(vu, MIN_DB, MAX_DB) / 20);
    return clamp((amplitude - SCALE_MIN) / (SCALE_MAX - SCALE_MIN), 0, 1);
  }

  function amplitudeToPosition(amplitude) {
    if (!Number.isFinite(amplitude) || amplitude <= SCALE_MIN) return 0;
    return clamp((amplitude - SCALE_MIN) / (SCALE_MAX - SCALE_MIN), 0, 1);
  }

  function positionToAngle(position) {
    return MIN_ANGLE + clamp(Number(position) || 0, 0, 1) * (MAX_ANGLE - MIN_ANGLE);
  }

  function vuToAngle(vu) {
    return positionToAngle(vuToPosition(vu));
  }

  function integrateMotion(motion, target, seconds) {
    if (seconds <= 0) return motion;
    // Exact underdamped solution, assuming target is constant over dt.
    const error = motion.position - target;
    const theta = DAMPED_FREQUENCY * seconds;
    const decay = Math.exp(-DECAY * seconds);
    const sine = Math.sin(theta);
    const cosine = Math.cos(theta);
    const position = target + decay * (error * cosine +
      (motion.velocity + DECAY * error) / DAMPED_FREQUENCY * sine);
    const velocity = decay * (motion.velocity * cosine -
      (NATURAL_FREQUENCY * NATURAL_FREQUENCY * error +
       DECAY * motion.velocity) / DAMPED_FREQUENCY * sine);
    return { position, velocity };
  }

  // Mechanical end stops: the coil cannot wind past the rest or the +3 VU
  // pin, so an overload releases from the pin instead of from far beyond it.
  function applyEndStops(motion) {
    if (motion.position > SCALE_MAX) return { position: SCALE_MAX, velocity: Math.min(0, motion.velocity) };
    if (motion.position < 0) return { position: 0, velocity: Math.max(0, motion.velocity) };
    return motion;
  }

  class Meter {
    constructor({ calibrationDbfs = -18, mode = 'classic' } = {}) {
      this.calibrationDbfs = Number.isFinite(calibrationDbfs) ? calibrationDbfs : -18;
      this.mode = mode === 'responsive' ? 'responsive' : 'classic';
      this.reset();
    }

    reset() {
      this.motion = { position: 0, velocity: 0 };
      this.lastTimeMs = null;
      this.baselineVu = null;
    }

    setMode(mode) {
      const next = mode === 'responsive' ? 'responsive' : 'classic';
      if (next !== this.mode) {
        this.mode = next;
        this.reset();
      }
    }

    setCalibration(dbfs) {
      if (Number.isFinite(dbfs) && dbfs !== this.calibrationDbfs) {
        this.calibrationDbfs = dbfs;
        this.reset();
      }
    }

    tick(sineRms, timeMs) {
      const now = Number.isFinite(timeMs) ? timeMs : 0;
      const seconds = this.lastTimeMs === null ? 0 :
        clamp((now - this.lastTimeMs) / 1000, 0, 0.25);
      this.lastTimeMs = now;
      const level = Number.isFinite(sineRms) && sineRms > 0 ? sineRms : 0;
      const reference = Math.pow(10, this.calibrationDbfs / 20);
      const linear = level / reference;
      const rawVu = linear > 1e-10 ? 20 * Math.log10(linear) : -Infinity;
      let target = linear;

      if (this.mode === 'responsive') {
        if (level > 1e-7) {
          if (this.baselineVu === null) this.baselineVu = rawVu;
          const weight = 1 - Math.exp(-seconds / 6);
          this.baselineVu += (rawVu - this.baselineVu) * weight;
          // Visual-only relative contrast, centred near -5 VU for
          // mastered music; does not synthesize movement for a flat signal.
          const displayVu = -5 + 2.2 * (rawVu - this.baselineVu);
          target = Math.pow(10, clamp(displayVu, -60, 12) / 20);
        } else {
          target = 0;
        }
      }

      this.motion = applyEndStops(integrateMotion(this.motion, target, seconds));
      const position = amplitudeToPosition(this.motion.position);
      return {
        position,
        angle: positionToAngle(position),
        rawVu,
        motionAmplitude: this.motion.position,
      };
    }
  }

  root.VelvetVU = Object.freeze({
    pcmLevel, analyserFftSize, mirrorSourceUrl, createPlayGate, vuToPosition, vuToAngle, positionToAngle,
    createMeter: options => new Meter(options),
    MIN_ANGLE, MAX_ANGLE,
  });
})(typeof globalThis !== 'undefined' ? globalThis : window);
