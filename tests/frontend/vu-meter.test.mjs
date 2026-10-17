import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Execute the exact browser classic-script implementation in isolation.
const sandbox = { Math, Number, Float32Array };
sandbox.globalThis = sandbox;
vm.runInNewContext(readFileSync('static/js/vu-meter-engine.js', 'utf8'), sandbox);
const VU = sandbox.VelvetVU;
const near = (a, b, tolerance = .02) =>
  assert.ok(Math.abs(a - b) < tolerance, `${a} expected ${b} +/- ${tolerance}`);

function sine(dbfs, samples = 4800, rate = 48000) {
  const peak = Math.SQRT2 * Math.pow(10, dbfs / 20);
  return Float32Array.from({ length: samples },
    (_, i) => peak * Math.sin(2 * Math.PI * 1000 * i / rate));
}
function step(dbfs, fps = 60, durationMs = 1000, options = {}) {
  const meter = VU.createMeter(options);
  const input = VU.pcmLevel(sine(dbfs)).sineRms;
  const frames = [];
  for (let i = 0; i <= durationMs / 1000 * fps; i++) {
    const t = i * 1000 / fps;
    frames.push({ t, ...meter.tick(input, t) });
  }
  return { meter, input, frames };
}

// -18 dBFS sine RMS => 0 VU alignment (NOT sine peak dBFS).
const measure = VU.pcmLevel(sine(-18));
near(20 * Math.log10(measure.sineRms), -18, .001);
near(20 * Math.log10(measure.rms), -18, .001);
near(VU.vuToAngle(-20), -60, 1e-9);
near(VU.vuToAngle(3), 60, 1e-9);
assert.ok(VU.vuToAngle(0) > 0 && VU.vuToAngle(0) < 60);
assert.ok(VU.vuToPosition(-7) < VU.vuToPosition(-3));

const sixty = step(-18);
const at300 = sixty.frames.find(x => Math.abs(x.t - 300) < .001);
near(at300.motionAmplitude, .99, .001);
const overshoot = Math.max(...sixty.frames.map(x => x.motionAmplitude));
assert.ok(overshoot >= 1.005 && overshoot <= 1.017, `overshoot=${overshoot}`);
near(sixty.frames.at(-1).angle, VU.vuToAngle(0), .25);
const thirty = step(-18, 30);
near(thirty.frames.at(-1).angle, sixty.frames.at(-1).angle, .05);
near(thirty.frames.find(x => Math.abs(x.t - 300) < .001).motionAmplitude,
  at300.motionAmplitude, .001);
const sixBelow = step(-24);
near(sixBelow.frames.at(-1).angle, VU.vuToAngle(-6), .3);

// Stop must RELEASE, not hold peaks or stick above 3/4 deflection.
let released;
for (let t = 1016; t <= 2016; t += 16) released = sixty.meter.tick(0, t);
assert.ok(released.angle <= -59.5, `release angle ${released.angle}`);

assert.ok(VU.pcmLevel(sine(-18)).sineRms / VU.pcmLevel(sine(-30)).sineRms > 3.9);
assert.equal(VU.pcmLevel(new Float32Array(64)).sineRms, 0);
assert.equal(VU.pcmLevel(Float32Array.from([NaN, Infinity])).sineRms, 0);

const calibrated = step(-12, 60, 1000, {calibrationDbfs: -12});
near(calibrated.frames.at(-1).angle, VU.vuToAngle(0), .3);
const responsive = VU.createMeter({mode:'responsive'});
const steady = Math.pow(10, -12 / 20);
let visual = responsive.tick(steady, 0);
for(let t=17;t<=1000;t+=17) visual = responsive.tick(steady,t);
const before = visual.angle;
for(let t=1017;t<=1167;t+=17) visual = responsive.tick(steady*1.4,t);
assert.ok(visual.angle > before + 8, 'real transients should move responsive mode');

// Integration checks: all units from PCM and all test resources present.
const html = readFileSync('static/index.html','utf8');
const sw = readFileSync('sw.js','utf8');
const harness = readFileSync('tests/frontend/inline-modules.mjs','utf8');
assert.ok(html.includes('/static/js/vu-meter-engine.js'));
assert.ok(html.includes('getFloatTimeDomainData('));
assert.ok(!html.includes('getByteFrequencyData('));
assert.ok(!html.includes('peakLeftAngle'));
assert.ok(html.includes('vu-calibration'));
assert.ok(html.includes('vu-mode-responsive'));
assert.ok(html.includes('vuSilentGain.connect(audioContext.destination)'));
assert.ok(sw.includes('/static/js/vu-meter-engine.js'));
assert.ok(harness.includes("'vu-meter-engine'"));
console.log('VU meter deterministic unit + integration checks: PASS');
