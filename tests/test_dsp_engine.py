"""EQ-1 regression tests: DSP validation, explicit bypass, combined headroom."""
import json
import math
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dsp_engine
from dsp_engine import (
    DspValidationError,
    build_dsp_filter_chain,
    dsp_effective_status,
    filters_response_db,
    peak_gain_db,
    profile_filters,
    recommended_preamp_db,
    resolve_dsp_plan,
    sanitize_dsp_filters,
    validate_dsp_filters,
    validate_dsp_headroom,
    validate_dsp_number,
    worst_case_preamp_db,
)

try:
    import numpy
except ImportError:  # pragma: no cover - numpy is optional for the FFmpeg check
    numpy = None


GRAPHIC_BANDS = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


class ExplicitEmptyChainTests(unittest.TestCase):
    LEGACY_BANDS = json.dumps([{"freq": 1000, "gain": 6, "q": 1.0}])

    def test_explicit_empty_chain_does_not_resurrect_legacy_bands(self):
        profile = {"filter_chain_json": "[]", "eq_bands": self.LEGACY_BANDS}
        self.assertEqual(profile_filters(profile), [])
        self.assertEqual(build_dsp_filter_chain(profile), "")

    def test_missing_chain_still_migrates_legacy_bands(self):
        for chain in (None, ""):
            with self.subTest(chain=chain):
                profile = {"filter_chain_json": chain, "eq_bands": self.LEGACY_BANDS}
                self.assertEqual(len(profile_filters(profile)), 1)

    def test_absent_chain_key_still_migrates_legacy_bands(self):
        self.assertEqual(len(profile_filters({"eq_bands": self.LEGACY_BANDS})), 1)


class StoredDataSanitizerTests(unittest.TestCase):
    def test_non_finite_stored_values_are_dropped_not_clamped_to_maximum(self):
        stored = ('[{"type":"peaking","freq":NaN,"gain":3,"q":1},'
                  '{"type":"peaking","freq":1000,"gain":Infinity,"q":1},'
                  '{"type":"peaking","freq":1000,"gain":2,"q":NaN},'
                  '{"type":"peaking","freq":1000,"gain":2,"q":1}]')
        self.assertEqual(sanitize_dsp_filters(stored),
                         [{"type": "peaking", "freq": 1000.0, "gain": 2.0, "q": 1.0, "enabled": True}])

    def test_non_finite_preamp_and_effects_never_reach_ffmpeg(self):
        chain = build_dsp_filter_chain({
            "preamp_db": float("nan"), "stereo_width": float("inf"),
            "reverb_room_size": float("nan"), "reverb_wet_dry": 50,
            "filter_chain_json": "[]",
        })
        self.assertNotIn("nan", chain.lower())
        self.assertNotIn("inf", chain.lower())


class StrictValidationTests(unittest.TestCase):
    VALID = {"type": "peaking", "freq": 1000, "gain": 3, "q": 1.0, "enabled": True}

    def _assert_rejected(self, flt):
        with self.assertRaises(DspValidationError):
            validate_dsp_filters([flt])

    def test_accepts_valid_filters_preserving_order_and_values(self):
        filters = [
            {"type": "low_shelf", "freq": 90, "gain": -0.5, "q": 0.7, "enabled": True},
            {"type": "PEAKING", "freq": "2500", "gain": "0.5", "q": "0.9"},
            {"type": "high_shelf", "freq": 10000, "gain": 0.4, "q": 0.7, "enabled": False},
        ]
        self.assertEqual(validate_dsp_filters(filters), [
            {"type": "low_shelf", "freq": 90.0, "gain": -0.5, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 2500.0, "gain": 0.5, "q": 0.9, "enabled": True},
            {"type": "high_shelf", "freq": 10000.0, "gain": 0.4, "q": 0.7, "enabled": False},
        ])

    def test_accepts_explicit_empty_list(self):
        self.assertEqual(validate_dsp_filters([]), [])
        self.assertEqual(validate_dsp_filters("[]"), [])

    def test_rejects_non_finite_values(self):
        for field in ("freq", "gain", "q"):
            for bad in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-inf"):
                with self.subTest(field=field, bad=bad):
                    self._assert_rejected({**self.VALID, field: bad})

    def test_rejects_non_numeric_and_wrong_types(self):
        for field in ("freq", "gain", "q"):
            for bad in ("oops", None, [], {}, True):
                with self.subTest(field=field, bad=bad):
                    self._assert_rejected({**self.VALID, field: bad})

    def test_rejects_out_of_range_instead_of_clamping(self):
        for field, bad in [("freq", -100), ("freq", 0), ("freq", 19.9), ("freq", 20001),
                           ("gain", 18.1), ("gain", -99), ("q", 0), ("q", 0.01), ("q", 10.5)]:
            with self.subTest(field=field, bad=bad):
                self._assert_rejected({**self.VALID, field: bad})

    def test_rejects_unknown_type_and_non_object_entries(self):
        self._assert_rejected({**self.VALID, "type": "notch"})
        with self.assertRaises(DspValidationError):
            validate_dsp_filters(["peaking"])
        with self.assertRaises(DspValidationError):
            validate_dsp_filters({"type": "peaking"})
        with self.assertRaises(DspValidationError):
            validate_dsp_filters("not json")

    def test_rejects_non_boolean_enabled(self):
        self._assert_rejected({**self.VALID, "enabled": "false"})

    def test_rejects_more_than_maximum_filter_count(self):
        validate_dsp_filters([self.VALID] * dsp_engine.MAX_FILTERS)
        with self.assertRaises(DspValidationError):
            validate_dsp_filters([self.VALID] * (dsp_engine.MAX_FILTERS + 1))

    def test_validate_number_rejects_non_finite_and_out_of_range(self):
        self.assertEqual(validate_dsp_number("-3.5", "preamp_db", -30, 6), -3.5)
        for bad in (float("nan"), "inf", "x", None, True, -31, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(DspValidationError):
                    validate_dsp_number(bad, "preamp_db", -30, 6)


class CombinedResponseTests(unittest.TestCase):
    def test_single_peaking_filter_peaks_at_its_gain(self):
        filters = [{"type": "peaking", "freq": 1000, "gain": 6, "q": 1.41, "enabled": True}]
        self.assertAlmostEqual(peak_gain_db(filters, 48000), 6.0, delta=0.05)

    def test_disabled_filters_do_not_contribute(self):
        filters = [{"type": "peaking", "freq": 1000, "gain": 6, "q": 1.41, "enabled": False}]
        self.assertAlmostEqual(peak_gain_db(filters, 48000), 0.0, delta=0.01)

    def test_shelf_peak_includes_ffmpeg_overshoot(self):
        # FFmpeg's w=0.7 shelf overshoots its nominal gain a little; headroom
        # must cover the real peak, not the nominal value.
        for flt in ({"type": "low_shelf", "freq": 100, "gain": 4, "q": 0.7, "enabled": True},
                    {"type": "high_shelf", "freq": 8000, "gain": 4, "q": 0.7, "enabled": True}):
            with self.subTest(type=flt["type"]):
                peak = peak_gain_db([flt], 48000)
                self.assertGreater(peak, 4.0)
                self.assertLess(peak, 4.6)

    def test_adjacent_boosts_sum_beyond_the_largest_single_gain(self):
        filters = [{"type": "peaking", "freq": f, "gain": 12, "q": 1.41, "enabled": True}
                   for f in (500, 1000, 2000)]
        peak = peak_gain_db(filters, 48000)
        self.assertGreater(peak, 13.0)
        preamp = recommended_preamp_db(filters, 48000, margin_db=1.0)
        self.assertAlmostEqual(preamp, -(peak + 1.0), delta=0.11)
        # The old heuristic capped at -12 dB; the combined response must not.
        self.assertLess(preamp, -12.0)

    def test_cuts_need_no_headroom(self):
        filters = [{"type": "peaking", "freq": 1000, "gain": -6, "q": 1.0, "enabled": True}]
        self.assertEqual(recommended_preamp_db(filters, 48000), 0.0)

    def test_resonant_shelf_near_nyquist_peak_is_not_missed_by_the_grid(self):
        # Codex review repro: FFmpeg measures +32.52 dB near 20.5 kHz; a fixed
        # log grid estimated only +28.36 dB.
        flt = [{"type": "high_shelf", "freq": 19900, "gain": 12, "q": 10, "enabled": True}]
        self.assertGreater(peak_gain_db(flt, 44100), 32.4)

    def test_narrow_peaks_between_grid_points_are_found(self):
        for flt in ({"type": "peaking", "freq": 20, "gain": 18, "q": 10, "enabled": True},
                    {"type": "peaking", "freq": 19999, "gain": 18, "q": 10, "enabled": True},
                    {"type": "low_shelf", "freq": 20, "gain": 18, "q": 10, "enabled": True}):
            with self.subTest(flt=flt):
                dense = [flt["freq"] * (1 + i / 20000) for i in range(-2000, 2001)]
                dense = [f for f in dense if 0 < f < 22050]
                brute = max(filters_response_db([flt], 44100, dense))
                self.assertGreaterEqual(peak_gain_db([flt], 44100), brute - 0.01)

    def test_response_grid_matches_requested_frequencies(self):
        filters = [{"type": "peaking", "freq": 1000, "gain": 6, "q": 1.0, "enabled": True}]
        response = filters_response_db(filters, 48000, [100, 1000, 10000])
        self.assertEqual(len(response), 3)
        self.assertAlmostEqual(response[1], 6.0, delta=0.01)
        self.assertLess(response[0], 1.0)


class ResolvedPlanTests(unittest.TestCase):
    def test_filters_at_or_above_nyquist_are_disabled_with_reason(self):
        profile = {"preamp_db": -2, "filter_chain_json": json.dumps([
            {"type": "peaking", "freq": 1000, "gain": 2, "q": 1.0, "enabled": True},
            {"type": "high_shelf", "freq": 12000, "gain": 3, "q": 0.7, "enabled": True},
        ])}
        plan = resolve_dsp_plan(profile, sample_rate=22050)
        self.assertEqual([f["freq"] for f in plan["filters"]], [1000.0])
        self.assertEqual(len(plan["disabled_filters"]), 1)
        self.assertIn("Nyquist", plan["disabled_filters"][0]["reason"])
        chain = build_dsp_filter_chain(profile, sample_rate=22050)
        self.assertIn("equalizer=f=1000.0", chain)
        self.assertNotIn("treble", chain)

    def test_plan_reports_headroom_and_guarded_net_peak(self):
        profile = {"preamp_db": -1, "filter_chain_json": json.dumps([
            {"type": "peaking", "freq": 1000, "gain": 6, "q": 1.0, "enabled": True},
        ])}
        plan = resolve_dsp_plan(profile, sample_rate=48000)
        self.assertAlmostEqual(plan["peak_gain_db"], 6.0, delta=0.05)
        # Saved -1 dB is insufficient: the guard adds attenuation to -7.0 dB.
        self.assertEqual(plan["effective_preamp_db"], -7.0)
        self.assertEqual(plan["headroom_guard_db"], -6.0)
        self.assertAlmostEqual(plan["net_peak_db"], -1.0, delta=0.05)
        self.assertFalse(plan["eq_clipping_risk"])
        self.assertNotIn("clipping_risk", plan)  # not a post-DSP measurement
        self.assertIn("not a post-DSP", plan["estimate_scope"])
        self.assertLessEqual(plan["recommended_preamp_db"], -6.0)
        self.assertTrue(plan["active"])

    def test_plan_without_boost_has_no_eq_clipping_risk(self):
        plan = resolve_dsp_plan({"preamp_db": -4, "filter_chain_json": json.dumps([
            {"type": "peaking", "freq": 1000, "gain": 3, "q": 1.0, "enabled": True},
        ])}, sample_rate=44100)
        self.assertFalse(plan["eq_clipping_risk"])

    def test_plan_without_processing_is_inactive(self):
        plan = resolve_dsp_plan({"preamp_db": 0, "filter_chain_json": "[]"}, sample_rate=44100)
        self.assertFalse(plan["active"])


CODEX_GEQ_GAINS = [4, -12, -4, 4, 12, 0, 0, 12, 12, 12]
CODEX_GEQ_FILTERS = [{"type": "peaking", "freq": f, "gain": g, "q": 1.41, "enabled": True}
                     for f, g in zip(GRAPHIC_BANDS, CODEX_GEQ_GAINS) if g]


class SampleRateHeadroomTests(unittest.TestCase):
    """Headroom must hold at the rate the EQ actually runs at, not just 48 kHz."""

    def test_worst_case_preamp_covers_every_reference_rate(self):
        # Codex review: +15.16 dB at 48 kHz but +16.96 dB at 192 kHz.
        self.assertAlmostEqual(peak_gain_db(CODEX_GEQ_FILTERS, 192000), 16.958, delta=0.01)
        preamp = worst_case_preamp_db(CODEX_GEQ_FILTERS)
        for rate in dsp_engine.DSP_REFERENCE_SAMPLE_RATES:
            playable = [f for f in CODEX_GEQ_FILTERS if f["freq"] < rate / 2]  # others are disabled
            with self.subTest(rate=rate):
                self.assertLessEqual(peak_gain_db(playable, rate) + preamp,
                                     -dsp_engine.DEFAULT_HEADROOM_MARGIN_DB + 1e-6)

    def test_playback_guard_adds_attenuation_when_saved_preamp_is_insufficient(self):
        profile = {"preamp_db": 0, "filter_chain_json": json.dumps(
            [{"type": "peaking", "freq": 1000, "gain": 12, "q": 1.0, "enabled": True}])}
        plan = resolve_dsp_plan(profile, sample_rate=44100)
        self.assertLessEqual(plan["effective_preamp_db"], -13.0)
        self.assertAlmostEqual(plan["headroom_guard_db"], plan["effective_preamp_db"], delta=1e-9)
        self.assertFalse(plan["eq_clipping_risk"])
        self.assertEqual(plan["preamp_db"], 0.0)  # the saved value is reported unchanged
        self.assertIn(f"volume={plan['effective_preamp_db']}dB", build_dsp_filter_chain(profile, 44100))

    def test_guard_tracks_the_actual_source_rate(self):
        profile = {"preamp_db": -16.2, "filter_chain_json": json.dumps(CODEX_GEQ_FILTERS)}
        at_48k = resolve_dsp_plan(profile, sample_rate=48000)
        at_192k = resolve_dsp_plan(profile, sample_rate=192000)
        self.assertEqual(at_48k["headroom_guard_db"], 0.0)
        self.assertLess(at_192k["headroom_guard_db"], 0.0)
        self.assertLessEqual(at_192k["net_peak_db"], -dsp_engine.DEFAULT_HEADROOM_MARGIN_DB + 0.01)

    def test_unknown_source_rate_uses_the_worst_case(self):
        profile = {"preamp_db": 0, "filter_chain_json": json.dumps(CODEX_GEQ_FILTERS)}
        plan = resolve_dsp_plan(profile)
        self.assertEqual(plan["effective_preamp_db"], worst_case_preamp_db(CODEX_GEQ_FILTERS))

    def test_saved_preamp_with_enough_headroom_is_left_alone(self):
        # Net peak -0.8 dB: inside the 1 dB recommendation, but not clipping.
        profile = {"preamp_db": -3.0, "filter_chain_json": json.dumps(
            [{"type": "peaking", "freq": 1000, "gain": 2.2, "q": 1.0, "enabled": True}])}
        self.assertEqual(resolve_dsp_plan(profile, 44100)["headroom_guard_db"], 0.0)

    def test_positive_preamp_without_boost_is_guarded(self):
        plan = resolve_dsp_plan({"preamp_db": 6, "filter_chain_json": "[]"}, 44100)
        self.assertEqual(plan["effective_preamp_db"], 0.0)
        self.assertEqual(plan["headroom_guard_db"], -6.0)

    def test_factory_presets_are_not_changed_by_the_guard(self):
        for preset in dsp_engine.FACTORY_DSP_PRESETS:
            for rate in dsp_engine.DSP_REFERENCE_SAMPLE_RATES:
                with self.subTest(preset=preset["name"], rate=rate):
                    self.assertEqual(resolve_dsp_plan(preset, rate)["headroom_guard_db"], 0.0)


class EffectiveStatusTests(unittest.TestCase):
    def test_named_empty_preset_is_reported_as_bypassed(self):
        status = dsp_effective_status(7, {"preamp_db": 0, "filter_chain_json": "[]"}, 44100)
        self.assertEqual(status["status"], "bypassed")
        self.assertIsNone(status["effective_profile_id"])
        self.assertEqual(status["requested_profile_id"], 7)

    def test_preset_with_processing_is_applied(self):
        status = dsp_effective_status(7, {"preamp_db": -3, "filter_chain_json": "[]"}, 44100)
        self.assertEqual(status["status"], "applied")
        self.assertEqual(status["effective_profile_id"], 7)

    def test_no_request_is_off(self):
        self.assertEqual(dsp_effective_status(None, None)["status"], "off")


class HeadroomPolicyTests(unittest.TestCase):
    def test_profiles_whose_boost_exceeds_available_preamp_are_rejected(self):
        for filters in (
            [{"type": "high_shelf", "freq": 19900, "gain": 12, "q": 10, "enabled": True}],
            [{"type": "peaking", "freq": 1000, "gain": 18, "q": 1.0, "enabled": True}] * 20,
        ):
            with self.subTest(n=len(filters)):
                with self.assertRaises(DspValidationError) as ctx:
                    validate_dsp_headroom(filters)
                self.assertIn("preamp", str(ctx.exception))

    def test_factory_presets_and_moderate_boosts_pass(self):
        for preset in dsp_engine.FACTORY_DSP_PRESETS:
            with self.subTest(preset=preset["name"]):
                validate_dsp_headroom(json.loads(preset["filter_chain_json"]))
        validate_dsp_headroom([{"type": "peaking", "freq": f, "gain": 12, "q": 1.41, "enabled": True}
                               for f in (500, 1000, 2000)])


def _random_adversarial_filter_sets(seed, count):
    """Valid filter sets biased toward high Q at the 20 Hz and near-Nyquist edges."""
    rng = random.Random(seed)
    cases = [(44100, [{"type": "high_shelf", "freq": 19900, "gain": 12, "q": 10, "enabled": True}])]
    for _ in range(count):
        rate = rng.choice([22050, 32000, 44100, 48000, 96000, 192000])
        top = min(20000.0, rate / 2 * 0.999)
        filters = []
        for _ in range(rng.randint(1, 6)):
            freq = rng.choice([rng.uniform(20, 40), rng.uniform(top * 0.75, top), 10 ** rng.uniform(1.3, 4.3)])
            filters.append({"type": rng.choice(["peaking", "low_shelf", "high_shelf"]),
                            "freq": round(min(freq, top), 2), "gain": round(rng.uniform(-18, 18), 2),
                            "q": rng.choice([10, 8, 5, round(rng.uniform(0.1, 10), 2)]), "enabled": True})
        cases.append((rate, filters))
    return cases


@unittest.skipUnless(numpy is not None, "numpy required")
class PeakSearchPropertyTests(unittest.TestCase):
    """The refined search must never underestimate a brute-force dense sweep."""

    def test_never_underestimates_brute_force_peak(self):
        for rate, filters in _random_adversarial_filter_sets(seed=1234, count=60):
            # Dense near DC (log) and near Nyquist (log distance), where warping squeezes peaks.
            freqs = numpy.concatenate([numpy.geomspace(0.5, rate / 2, 200_000),
                                       rate / 2 - numpy.geomspace(1e-3, rate / 2, 200_000), [0.0, rate / 2]])
            z1 = numpy.exp(-2j * numpy.pi * freqs / rate)
            response = numpy.ones_like(z1)
            for b0, b1, b2, a0, a1, a2 in dsp_engine._active_coefficients(filters, rate):
                response *= (b0 + b1 * z1 + b2 * z1 * z1) / (a0 + a1 * z1 + a2 * z1 * z1)
            brute = max(0.0, float(20 * numpy.log10(numpy.abs(response).max())))
            with self.subTest(rate=rate, filters=filters):
                self.assertGreaterEqual(peak_gain_db(filters, rate), brute - 0.01)


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class JavaScriptParityTests(unittest.TestCase):
    """static/js/dsp-controls.js must compute the same peaks/headroom as Python."""

    def _run_js(self, expression, cases):
        """Evaluate `expression` over `cases` with dsp-controls.js loaded."""
        script = Path(__file__).resolve().parents[1] / "static" / "js" / "dsp-controls.js"
        runner = (
            "const vm=require('vm'),fs=require('fs');"
            "const ctx=vm.createContext({window:{},console,GEQ_BANDS:[],GEQ_DEFAULT_Q:1.41,currentEqGains:[]});"
            f"vm.runInContext(fs.readFileSync({json.dumps(str(script))},'utf8'),ctx);"
            "const cases=JSON.parse(fs.readFileSync(0,'utf8'));"
            "ctx.cases=cases;"
            f"process.stdout.write(JSON.stringify(vm.runInContext({json.dumps(expression)},ctx)));"
        )
        result = subprocess.run(["node", "-e", runner], input=json.dumps(cases),
                                capture_output=True, text=True, check=True)
        values = json.loads(result.stdout)
        self.assertEqual(len(values), len(cases))
        return values

    def test_js_peak_and_preamp_match_python(self):
        cases = _random_adversarial_filter_sets(seed=99, count=40)
        js_peaks = self._run_js("cases.map(([r,f])=>[dspPeakGainDb(f,r),dspWorstCasePreampDb(f)])", cases)
        for (rate, filters), (js_peak, js_preamp) in zip(cases, js_peaks):
            with self.subTest(rate=rate, filters=filters):
                self.assertAlmostEqual(js_peak, peak_gain_db(filters, rate), delta=0.01)
                self.assertAlmostEqual(js_preamp, worst_case_preamp_db(filters), delta=0.11)

    def test_js_curve_response_matches_python(self):
        # The EQ editor plots dspFiltersResponseDb; shelves must keep FFmpeg's Q*sqrt(2).
        freqs = [20, 31.5, 47, 90, 110, 1000, 3150, 9500, 15000, 20000]
        cases = [(rate, filters, [f for f in freqs if f < rate / 2])
                 for rate, filters in _random_adversarial_filter_sets(seed=7, count=20)]
        js_curves = self._run_js("cases.map(([r,f,q])=>dspFiltersResponseDb(f,r,q))", cases)
        for (rate, filters, points), js_curve in zip(cases, js_curves):
            with self.subTest(rate=rate, filters=filters):
                for got, want in zip(js_curve, filters_response_db(filters, rate, points)):
                    self.assertAlmostEqual(got, want, delta=1e-6)

    def test_js_effective_preamp_matches_server_plan(self):
        # The editor's preamp line is the preamp the server applies at an
        # unknown source rate, including the playback headroom guard.
        rng = random.Random(5)
        cases = []
        for _, filters in _random_adversarial_filter_sets(seed=11, count=30):
            for flt in filters:
                flt["enabled"] = rng.random() > 0.2
            cases.append((filters, rng.choice([0.0, 2.0, -1.0, -6.0, round(rng.uniform(-30, 6), 1)])))
        cases.append(([{"type": "peaking", "freq": 1000, "gain": 9, "q": 1, "enabled": True}], -2.0))
        js_preamps = self._run_js("cases.map(([f,p])=>dspEffectivePreampDb(f,p))", cases)
        for (filters, preamp), js_preamp in zip(cases, js_preamps):
            with self.subTest(preamp=preamp, filters=filters):
                plan = resolve_dsp_plan({"preamp_db": preamp, "filter_chain_json": json.dumps(filters)})
                self.assertAlmostEqual(js_preamp, plan["effective_preamp_db"], delta=0.11)


@unittest.skipUnless(shutil.which("ffmpeg") and numpy is not None, "FFmpeg and numpy required")
class FFmpegResponseMatchTests(unittest.TestCase):
    """The model must match what FFmpeg actually does to audio (<=0.5 dB)."""

    def _ffmpeg_spectrum(self, chain, rate, length=1 << 16, fft_length=None):
        impulse = numpy.zeros(length, dtype="<f8")
        impulse[0] = 1.0
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "f64le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
             "-af", chain, "-f", "f64le", "-ac", "1", "pipe:1"],
            input=impulse.tobytes(), capture_output=True, check=True)
        out = numpy.frombuffer(result.stdout, dtype="<f8")[:length]
        fft_length = fft_length or length  # zero-padding interpolates narrow peaks
        return abs(numpy.fft.rfft(out, fft_length)), numpy.fft.rfftfreq(fft_length, 1.0 / rate)

    def _ffmpeg_response_db(self, chain, rate, freqs):
        spectrum, bins = self._ffmpeg_spectrum(chain, rate)
        return [20 * math.log10(spectrum[int(numpy.argmin(abs(bins - f)))]) for f in freqs]

    def test_predicted_peak_covers_the_actual_ffmpeg_maximum(self):
        # Adversarial but valid filters: resonant shelves/peaks at the band edges.
        cases = [
            [{"type": "high_shelf", "freq": 19900, "gain": 12, "q": 10, "enabled": True}],
            [{"type": "high_shelf", "freq": 15000, "gain": 6, "q": 10, "enabled": True}],
            [{"type": "low_shelf", "freq": 20, "gain": 12, "q": 10, "enabled": True}],
            [{"type": "peaking", "freq": 20, "gain": 18, "q": 10, "enabled": True}],
            [{"type": "peaking", "freq": 19990, "gain": 18, "q": 10, "enabled": True}],
            [{"type": "peaking", "freq": f, "gain": 12, "q": 1.41, "enabled": True}
             for f in (500, 1000, 2000)],
        ]
        for filters in cases:
            chain = build_dsp_filter_chain({"filter_chain_json": json.dumps(filters)})
            for rate in (44100, 48000, 96000):
                with self.subTest(filters=filters[0], rate=rate):
                    spectrum, _ = self._ffmpeg_spectrum(chain, rate, 1 << 18, 1 << 22)
                    guard = resolve_dsp_plan({"filter_chain_json": json.dumps(filters)}, rate)
                    actual = 20 * math.log10(float(spectrum.max())) - guard["effective_preamp_db"]
                    predicted = peak_gain_db(filters, rate)
                    self.assertGreaterEqual(predicted, actual - 0.05)
                    self.assertLessEqual(predicted, actual + 0.5)

    def test_guarded_chain_output_stays_below_full_scale_at_every_rate(self):
        # On-wire check of the whole FFmpeg chain (preamp + EQ), Codex GEQ
        # counterexample saved with the old 48 kHz-only preamp of -16.2 dB.
        profile = {"preamp_db": -16.2, "filter_chain_json": json.dumps(CODEX_GEQ_FILTERS)}
        for rate in (44100, 48000, 96000, 192000, 384000):
            with self.subTest(rate=rate):
                chain = build_dsp_filter_chain(profile, rate)
                spectrum, _ = self._ffmpeg_spectrum(chain, rate, 1 << 18, 1 << 20)
                self.assertLessEqual(20 * math.log10(float(spectrum.max())), -0.95)

    def test_model_matches_ffmpeg_for_all_filter_types(self):
        filters = [
            {"type": "low_shelf", "freq": 90, "gain": 3.5, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 1000, "gain": 6, "q": 1.41, "enabled": True},
            {"type": "peaking", "freq": 2000, "gain": -4, "q": 2.0, "enabled": True},
            {"type": "high_shelf", "freq": 9000, "gain": -3, "q": 0.7, "enabled": True},
        ]
        chain = build_dsp_filter_chain({"filter_chain_json": json.dumps(filters)})
        for rate in (44100, 48000, 96000, 192000):
            freqs = [f for f in (30, 90, 300, 1000, 1500, 2000, 5000, 9000, 15000, 20000)
                     if f < rate / 2]
            measured = self._ffmpeg_response_db(chain, rate, freqs)
            preamp = resolve_dsp_plan({"filter_chain_json": json.dumps(filters)}, rate)["effective_preamp_db"]
            modeled = [db + preamp for db in filters_response_db(filters, rate, freqs)]
            for freq, got, want in zip(freqs, measured, modeled):
                with self.subTest(rate=rate, freq=freq):
                    self.assertAlmostEqual(got, want, delta=0.5)

    def test_imported_autoeq_profile_matches_autoeq_intended_response(self):
        # AutoEq designs its filters as plain RBJ biquads (autoeq/peq.py); the
        # import must make FFmpeg render exactly that curve, shelves included.
        imported = dsp_engine.parse_autoeq_parametric_eq(AUTOEQ_SAMPLE)
        profile = {"preamp_db": imported["preamp_db"], "filter_chain_json": json.dumps(imported["filters"])}
        for rate in (44100, 48000, 96000):
            spectrum, bins = self._ffmpeg_spectrum(build_dsp_filter_chain(profile, rate), rate)
            preamp = resolve_dsp_plan(profile, rate)["effective_preamp_db"]
            for freq in (20, 50, 105, 156, 400, 1340, 2840, 4980, 7650, 10000, 16000, 20000):
                index = int(numpy.argmin(abs(bins - freq)))  # compare at the FFT bin itself
                got = 20 * math.log10(spectrum[index])
                want = _autoeq_reference_db(AUTOEQ_SAMPLE_FILTERS, rate, float(bins[index])) + preamp
                with self.subTest(rate=rate, freq=freq):
                    self.assertAlmostEqual(got, want, delta=0.01)

    def test_replaygain_tags_are_read_from_a_real_flac_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tagged.flac"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.1",
                 "-metadata", "REPLAYGAIN_TRACK_GAIN=-6.53 dB", "-metadata", "REPLAYGAIN_TRACK_PEAK=0.988",
                 "-metadata", "REPLAYGAIN_ALBUM_GAIN=+1.20 dB", "-metadata", "REPLAYGAIN_ALBUM_PEAK=0.5",
                 str(path)], check=True)
            tags = dsp_engine.read_replaygain_tags(path)
        self.assertEqual(tags, {"track_gain": -6.53, "track_peak": 0.988,
                                "album_gain": 1.2, "album_peak": 0.5})


# A synthetic profile in AutoEq's ParametricEQ.txt layout (both shelf types,
# a disabled filter, CRLF line ends and the Q precision AutoEq writes).
AUTOEQ_SAMPLE = (
    "Preamp: -6.4 dB\r\n"
    "Filter 1: ON LSC Fc 105 Hz Gain 6.3 dB Q 0.70\r\n"
    "Filter 2: ON PK Fc 156 Hz Gain -2.1 dB Q 0.53\r\n"
    "Filter 3: ON PK Fc 2840 Hz Gain 3.5 dB Q 2.43\r\n"
    "Filter 4: ON PK Fc 4980 Hz Gain -2.2 dB Q 3.89\r\n"
    "Filter 5: ON PK Fc 7650 Hz Gain -3.2 dB Q 2.66\r\n"
    "Filter 6: ON HSC Fc 10000 Hz Gain -2.4 dB Q 0.70\r\n"
    "Filter 7: ON PK Fc 1340 Hz Gain -0.9 dB Q 1.66\r\n"
    "Filter 8: OFF PK Fc 3720 Hz Gain 1.1 dB Q 4.67\r\n"
)
AUTOEQ_SAMPLE_FILTERS = [  # (enabled, type, Fc, gain, AutoEq Q)
    (True, "LSC", 105, 6.3, 0.70), (True, "PK", 156, -2.1, 0.53), (True, "PK", 2840, 3.5, 2.43),
    (True, "PK", 4980, -2.2, 3.89), (True, "PK", 7650, -3.2, 2.66), (True, "HSC", 10000, -2.4, 0.70),
    (True, "PK", 1340, -0.9, 1.66), (False, "PK", 3720, 1.1, 4.67),
]


def _autoeq_reference_db(filters, rate, freq):
    """AutoEq's own response: RBJ cookbook biquads with alpha = sin(w0)/(2Q)."""
    total = 1 + 0j
    z1 = complex(math.cos(2 * math.pi * freq / rate), -math.sin(2 * math.pi * freq / rate))
    for enabled, kind, fc, gain, q in filters:
        if not enabled:
            continue
        a = 10 ** (gain / 40)
        w0 = 2 * math.pi * fc / rate
        cos_w0, alpha = math.cos(w0), math.sin(w0) / (2 * q)
        root = 2 * math.sqrt(a) * alpha
        if kind == "LSC":
            b = (a * ((a + 1) - (a - 1) * cos_w0 + root), 2 * a * ((a - 1) - (a + 1) * cos_w0),
                 a * ((a + 1) - (a - 1) * cos_w0 - root))
            den = ((a + 1) + (a - 1) * cos_w0 + root, -2 * ((a - 1) + (a + 1) * cos_w0),
                   (a + 1) + (a - 1) * cos_w0 - root)
        elif kind == "HSC":
            b = (a * ((a + 1) + (a - 1) * cos_w0 + root), -2 * a * ((a - 1) + (a + 1) * cos_w0),
                 a * ((a + 1) + (a - 1) * cos_w0 - root))
            den = ((a + 1) - (a - 1) * cos_w0 + root, 2 * ((a - 1) - (a + 1) * cos_w0),
                   (a + 1) - (a - 1) * cos_w0 - root)
        else:
            b = (1 + alpha * a, -2 * cos_w0, 1 - alpha * a)
            den = (1 + alpha / a, -2 * cos_w0, 1 - alpha / a)
        total *= (b[0] + b[1] * z1 + b[2] * z1 * z1) / (den[0] + den[1] * z1 + den[2] * z1 * z1)
    return 20 * math.log10(abs(total))


class AutoEqImportTests(unittest.TestCase):
    parse = staticmethod(lambda text: dsp_engine.parse_autoeq_parametric_eq(text))
    export = staticmethod(lambda preamp, filters: dsp_engine.format_autoeq_parametric_eq(preamp, filters))

    def test_maps_filter_types_and_shelf_q_exactly_in_file_order(self):
        imported = self.parse(AUTOEQ_SAMPLE)
        self.assertEqual(imported["preamp_db"], -6.4)
        types = {"PK": "peaking", "LSC": "low_shelf", "HSC": "high_shelf"}
        expected = [{"type": types[kind], "freq": float(fc), "gain": gain,
                     "q": q / math.sqrt(2) if kind != "PK" else q, "enabled": enabled}
                    for enabled, kind, fc, gain, q in AUTOEQ_SAMPLE_FILTERS]
        self.assertEqual(imported["filters"], expected)
        # The stored filters pass strict validation unchanged.
        self.assertEqual(validate_dsp_filters(imported["filters"]), imported["filters"])

    def test_model_curve_is_autoeq_curve(self):
        filters = self.parse(AUTOEQ_SAMPLE)["filters"]
        for rate in (44100, 48000, 192000):
            freqs = [20, 60, 105, 300, 1000, 2840, 7650, 10000, 15000, 20000]
            for freq, got in zip(freqs, filters_response_db(filters, rate, freqs)):
                with self.subTest(rate=rate, freq=freq):
                    self.assertAlmostEqual(got, _autoeq_reference_db(AUTOEQ_SAMPLE_FILTERS, rate, freq),
                                           delta=1e-9)

    def test_export_then_import_is_exact(self):
        imported = self.parse(AUTOEQ_SAMPLE)
        text = self.export(imported["preamp_db"], imported["filters"])
        self.assertIn("Filter 1: ON LSC Fc 105 Hz Gain 6.3 dB Q 0.7\n", text)
        self.assertIn("Filter 8: OFF PK Fc 3720 Hz Gain 1.1 dB Q 4.67\n", text)
        self.assertTrue(text.startswith("Preamp: -6.4 dB\n"))
        self.assertEqual(self.parse(text), imported)
        # And through JSON storage, as the server keeps it.
        stored = json.loads(json.dumps(imported["filters"]))
        self.assertEqual(self.parse(self.export(imported["preamp_db"], stored))["filters"], stored)

    def test_export_round_trip_for_arbitrary_stored_values(self):
        # Exact except a shelf q typed into VELVET that no AutoEq Q maps to
        # (AutoEq's Q is the coarser double): that one is off by <= 1 ulp, and
        # from then on the round trip is exact.
        rng = random.Random(42)
        for _ in range(20):
            filters = [{"type": rng.choice(["peaking", "low_shelf", "high_shelf"]),
                        "freq": rng.uniform(20, 20000), "gain": rng.uniform(-3, 3),
                        "q": rng.uniform(0.1, 10), "enabled": rng.random() > 0.2}
                       for _ in range(rng.randint(1, 6))]
            preamp = rng.uniform(-30, 6)
            with self.subTest(filters=filters):
                once = self.parse(self.export(preamp, filters))
                self.assertEqual(once["preamp_db"], preamp)
                for got, want in zip(once["filters"], filters, strict=True):
                    self.assertEqual({**got, "q": None}, {**want, "q": None})
                    if want["type"] == "peaking":
                        self.assertEqual(got["q"], want["q"])
                    else:
                        self.assertLessEqual(abs(got["q"] - want["q"]), math.ulp(want["q"]))
                self.assertEqual(self.parse(self.export(preamp, once["filters"])), once)
        # Every shelf q that came from AutoEq text is reproduced exactly.
        for _ in range(150):
            q = float(f"{rng.uniform(0.15, 14.1):.{rng.randint(1, 17)}g}") / math.sqrt(2)
            flt = [{"type": "low_shelf", "freq": 100.0, "gain": 1.0, "q": q, "enabled": True}]
            self.assertEqual(self.parse(self.export(0.0, flt))["filters"], flt)

    def test_accepts_autoeq_layout_variants(self):
        text = ("﻿# AutoEq: Example headphone\n\n  Preamp:-3.0 dB  \n"
                "Filter 1: ON PK Fc 1000 Hz Gain 2 dB Q 1\nFilter 2: on lsc Fc 1e2 Hz Gain -1.5 dB Q .7\n")
        imported = self.parse(text)
        self.assertEqual(imported["preamp_db"], -3.0)
        self.assertEqual([f["type"] for f in imported["filters"]], ["peaking", "low_shelf"])
        self.assertEqual(imported["filters"][1]["freq"], 100.0)
        # No Preamp line: 0 dB, the playback headroom guard still applies.
        self.assertEqual(self.parse("Filter 1: ON PK Fc 1000 Hz Gain -2 dB Q 1")["preamp_db"], 0.0)

    def test_rejects_anything_it_cannot_map_exactly(self):
        pk = "Filter 1: ON PK Fc 1000 Hz Gain 2 dB Q 1"
        cases = {
            "": "no filters",
            "Preamp: -3 dB": "no filters",
            f"{pk}\nFilter 2: ON LS Fc 100 Hz Gain 2 dB": "line 2",
            f"{pk}\nFilter 2: ON LP Fc 100 Hz Q 0.7": "line 2",
            f"{pk}\nFilter 2: ON PK Fc 1000 Hz Gain 3 dB": "line 2",
            f"{pk}\nChannel: L": "line 2",
            f"{pk}\nhello": "line 2",
            f"{pk}\n{pk}": "line 2",  # duplicate filter number
            f"Preamp: -1 dB\nPreamp: -2 dB\n{pk}": "line 2",
            "Filter 1: ON PK Fc 1000 Hz Gain nan dB Q 1": "line 1",
            "Filter 1: ON PK Fc 10 Hz Gain 2 dB Q 1": "line 1",
            "Filter 1: ON PK Fc 1000 Hz Gain 19 dB Q 1": "line 1",
            "Filter 1: ON PK Fc 1000 Hz Gain 2 dB Q 0.05": "line 1",
            "Filter 1: ON HSC Fc 8000 Hz Gain 2 dB Q 0.1": "line 1",  # AutoEq Q 0.1 = q 0.0707
            "Filter 1: ON HSC Fc 8000 Hz Gain 2 dB Q 15": "line 1",
            f"Preamp: -31 dB\n{pk}": "line 1",
            f"Preamp: inf dB\n{pk}": "line 1",
            "\n".join(f"Filter {i}: ON PK Fc 1000 Hz Gain 1 dB Q 1" for i in range(1, 22)): "at most 20",
        }
        for text, message in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(DspValidationError) as ctx:
                    self.parse(text)
                self.assertIn(message, str(ctx.exception))

    def test_rejects_profiles_whose_boost_cannot_be_offset(self):
        text = "\n".join(f"Filter {i}: ON PK Fc 1000 Hz Gain 18 dB Q 1" for i in range(1, 4))
        with self.assertRaises(DspValidationError) as ctx:
            self.parse(text)
        self.assertIn("preamp", str(ctx.exception))

    def test_shelf_q_limits_follow_the_velvet_q_range(self):
        # VELVET q 0.1-10 is AutoEq shelf Q 0.141-14.14.
        self.assertEqual(self.parse("Filter 1: ON LSC Fc 100 Hz Gain 2 dB Q 0.15")["filters"][0]["q"],
                         0.15 / math.sqrt(2))
        self.parse("Filter 1: ON HSC Fc 8000 Hz Gain 2 dB Q 14.1")


class ReplayGainTests(unittest.TestCase):
    def test_tag_parsing_across_containers_and_r128(self):
        items = [
            ("TXXX:REPLAYGAIN_TRACK_GAIN", "-6.53 dB"),            # ID3
            ("replaygain_track_peak", "0.988235"),                  # Vorbis comment
            ("----:com.apple.iTunes:replaygain_album_gain", b"+2.10 dB"),  # MP4 freeform
            ("REPLAYGAIN_ALBUM_PEAK", ["0.5", "0.9"]),             # first value wins
            ("TITLE", "Song"),
        ]
        self.assertEqual(dsp_engine.replaygain_from_tags(items),
                         {"track_gain": -6.53, "track_peak": 0.988235, "album_gain": 2.1, "album_peak": 0.5})
        # Opus R128 gains are Q7.8 relative to -23 LUFS; ReplayGain 2.0 is 5 dB louder.
        self.assertEqual(dsp_engine.replaygain_from_tags([("R128_TRACK_GAIN", "-1024"), ("R128_ALBUM_GAIN", "256")]),
                         {"track_gain": 1.0, "album_gain": 6.0})
        # ReplayGain tags take precedence over R128.
        self.assertEqual(dsp_engine.replaygain_from_tags([("R128_TRACK_GAIN", "0"), ("REPLAYGAIN_TRACK_GAIN", "-3 dB")]),
                         {"track_gain": -3.0})
        # Unparseable, non-finite or absurd values are ignored, never guessed.
        for value in ("loud", "nan dB", "inf", "-99 dB", ""):
            with self.subTest(value=value):
                self.assertEqual(dsp_engine.replaygain_from_tags([("REPLAYGAIN_TRACK_GAIN", value)]), {})
        self.assertEqual(dsp_engine.replaygain_from_tags([("REPLAYGAIN_TRACK_PEAK", "0")]), {})

    def test_mode_selection_and_album_fallback(self):
        tags = {"track_gain": -6.5, "track_peak": 0.9, "album_gain": -7.25, "album_peak": 0.99}
        resolve = dsp_engine.resolve_replaygain
        self.assertEqual(resolve(tags, "off")["applied_db"], 0.0)
        self.assertEqual(resolve(tags, "track")["applied_db"], -6.5)
        album = resolve(tags, "album")
        self.assertEqual((album["source"], album["applied_db"]), ("album", -7.25))
        fallback = resolve({"track_gain": -4.0}, "album")
        self.assertEqual((fallback["source"], fallback["applied_db"]), ("track", -4.0))
        self.assertIn("no album gain", fallback["reason"])
        none = resolve({}, "album")
        self.assertEqual((none["source"], none["applied_db"]), (None, 0.0))
        self.assertIn("No ReplayGain", none["reason"])
        with self.assertRaises(DspValidationError):
            resolve(tags, "loud")

    def test_positive_gain_is_limited_by_the_tag_peak(self):
        # The source must stay at or below full scale after ReplayGain: the EQ
        # headroom guard assumes full-scale input.
        limited = dsp_engine.resolve_replaygain({"track_gain": 6.0, "track_peak": 0.6}, "track")
        self.assertAlmostEqual(limited["applied_db"], -20 * math.log10(0.6))
        self.assertTrue(limited["limited_by_peak"])
        self.assertEqual(dsp_engine.resolve_replaygain({"track_gain": 2.0, "track_peak": 0.5}, "track")["applied_db"],
                         2.0)
        # No peak tag: a positive gain cannot be proven safe, so none is applied.
        unknown = dsp_engine.resolve_replaygain({"track_gain": 3.0}, "track")
        self.assertEqual(unknown["applied_db"], 0.0)
        self.assertTrue(unknown["limited_by_peak"])
        # A peak above full scale (lossy overshoot) lowers even a negative gain.
        hot = dsp_engine.resolve_replaygain({"track_gain": -1.0, "track_peak": 1.25}, "track")
        self.assertAlmostEqual(hot["applied_db"], -20 * math.log10(1.25))

    def test_chain_applies_replaygain_before_the_preamp_and_eq(self):
        self.assertEqual(build_dsp_filter_chain(None, 44100, replaygain_db=-6.53),
                         "volume=-6.53dB:precision=double")
        self.assertEqual(build_dsp_filter_chain(None, 44100), "")
        profile = {"preamp_db": -3.0,
                   "filter_chain_json": json.dumps([{"type": "peaking", "freq": 1000, "gain": 1.5, "q": 1,
                                                     "enabled": True}])}
        self.assertEqual(build_dsp_filter_chain(profile, 44100, replaygain_db=-6.53),
                         "volume=-6.53dB:precision=double,volume=-3.0dB:precision=double,"
                         "equalizer=f=1000.0:t=q:w=1.0:g=1.5")


class LoudnormRetiredTests(unittest.TestCase):
    """volume_normalize used single-pass loudnorm: a dynamic AGC outside the
    headroom estimate that also resampled the stream to 192 kHz."""

    def test_volume_normalize_no_longer_adds_loudnorm(self):
        profile = {"volume_normalize": 1, "preamp_db": -2.0,
                   "filter_chain_json": json.dumps([{"type": "peaking", "freq": 1000, "gain": 1, "q": 1}])}
        chain = build_dsp_filter_chain(profile, 44100)
        self.assertNotIn("loudnorm", chain)
        plan = resolve_dsp_plan(profile, 44100)
        self.assertNotIn("loudnorm", plan["level_changing_effects"])
        self.assertEqual(plan["ignored_settings"], ["volume_normalize"])
        self.assertFalse(resolve_dsp_plan({"volume_normalize": 1, "filter_chain_json": "[]"})["active"])


if __name__ == "__main__":
    unittest.main()
