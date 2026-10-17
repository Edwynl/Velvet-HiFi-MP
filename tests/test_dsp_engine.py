"""EQ-1 regression tests: DSP validation, explicit bypass, combined headroom."""
import json
import math
import shutil
import subprocess
import unittest

import dsp_engine
from dsp_engine import (
    DspValidationError,
    build_dsp_filter_chain,
    filters_response_db,
    peak_gain_db,
    profile_filters,
    recommended_preamp_db,
    resolve_dsp_plan,
    sanitize_dsp_filters,
    validate_dsp_filters,
    validate_dsp_number,
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

    def test_plan_reports_headroom_and_clipping_risk(self):
        profile = {"preamp_db": -1, "filter_chain_json": json.dumps([
            {"type": "peaking", "freq": 1000, "gain": 6, "q": 1.0, "enabled": True},
        ])}
        plan = resolve_dsp_plan(profile, sample_rate=48000)
        self.assertAlmostEqual(plan["peak_gain_db"], 6.0, delta=0.05)
        self.assertAlmostEqual(plan["net_peak_db"], 5.0, delta=0.05)
        self.assertTrue(plan["clipping_risk"])
        self.assertLessEqual(plan["recommended_preamp_db"], -6.0)

    def test_plan_without_boost_has_no_clipping_risk(self):
        plan = resolve_dsp_plan({"preamp_db": -4, "filter_chain_json": json.dumps([
            {"type": "peaking", "freq": 1000, "gain": 3, "q": 1.0, "enabled": True},
        ])}, sample_rate=44100)
        self.assertFalse(plan["clipping_risk"])


@unittest.skipUnless(shutil.which("ffmpeg") and numpy is not None, "FFmpeg and numpy required")
class FFmpegResponseMatchTests(unittest.TestCase):
    """The model must match what FFmpeg actually does to audio (<=0.5 dB)."""

    def _ffmpeg_response_db(self, chain, rate, freqs):
        length = 1 << 16
        impulse = numpy.zeros(length, dtype="<f8")
        impulse[0] = 1.0
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "f64le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
             "-af", chain, "-f", "f64le", "-ac", "1", "pipe:1"],
            input=impulse.tobytes(), capture_output=True, check=True)
        out = numpy.frombuffer(result.stdout, dtype="<f8")[:length]
        spectrum = numpy.fft.rfft(out)
        bins = numpy.fft.rfftfreq(length, 1.0 / rate)
        return [20 * math.log10(abs(spectrum[int(numpy.argmin(abs(bins - f)))])) for f in freqs]

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
            modeled = filters_response_db(filters, rate, freqs)
            for freq, got, want in zip(freqs, measured, modeled):
                with self.subTest(rate=rate, freq=freq):
                    self.assertAlmostEqual(got, want, delta=0.5)


if __name__ == "__main__":
    unittest.main()
