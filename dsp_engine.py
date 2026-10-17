import cmath
import json
import math
from typing import Any


VALID_FILTER_TYPES = {"peaking", "low_shelf", "high_shelf"}
MIN_FILTER_FREQ = 20.0
MAX_FILTER_FREQ = 20000.0
MIN_FILTER_GAIN = -18.0
MAX_FILTER_GAIN = 18.0
MIN_FILTER_Q = 0.1
MAX_FILTER_Q = 10.0
MAX_FILTERS = 20
MIN_PREAMP_DB = -30.0
MAX_PREAMP_DB = 6.0
DEFAULT_HEADROOM_MARGIN_DB = 1.0
# The playback guard leaves a saved preamp alone while it keeps at least this
# much headroom; below it, the recommended preamp (full margin) is applied.
HEADROOM_GUARD_MIN_DB = 0.5


class DspValidationError(ValueError):
    """A DSP payload is not safe to store or apply; never silently repaired."""


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _finite_float(value: Any) -> float | None:
    """float(value) when it is a finite number (or numeric string), else None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_dsp_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    number = _finite_float(value)
    if number is None:
        raise DspValidationError(f"{field} must be a finite number")
    if not minimum <= number <= maximum:
        raise DspValidationError(f"{field} must be between {minimum:g} and {maximum:g}")
    return number


def validate_dsp_filters(raw_filters: Any) -> list[dict[str, Any]]:
    """Strictly validate user-supplied filters; reject instead of clamping/dropping."""
    filters = raw_filters
    if isinstance(raw_filters, str):
        try:
            filters = json.loads(raw_filters)
        except json.JSONDecodeError as exc:
            raise DspValidationError("filters must be a JSON list") from exc
    if not isinstance(filters, list):
        raise DspValidationError("filters must be a list")
    if len(filters) > MAX_FILTERS:
        raise DspValidationError(f"at most {MAX_FILTERS} filters are allowed")

    validated: list[dict[str, Any]] = []
    for index, flt in enumerate(filters, start=1):
        label = f"filter {index}"
        if not isinstance(flt, dict):
            raise DspValidationError(f"{label} must be an object")
        filter_type = flt.get("type", "peaking")
        if not isinstance(filter_type, str) or filter_type.lower() not in VALID_FILTER_TYPES:
            raise DspValidationError(f"{label}: type must be one of {sorted(VALID_FILTER_TYPES)}")
        enabled = flt.get("enabled", True)
        if not isinstance(enabled, bool):
            raise DspValidationError(f"{label}: enabled must be true or false")
        validated.append({
            "type": filter_type.lower(),
            "freq": validate_dsp_number(flt.get("freq"), f"{label}: freq", MIN_FILTER_FREQ, MAX_FILTER_FREQ),
            "gain": validate_dsp_number(flt.get("gain"), f"{label}: gain", MIN_FILTER_GAIN, MAX_FILTER_GAIN),
            "q": validate_dsp_number(flt.get("q"), f"{label}: q", MIN_FILTER_Q, MAX_FILTER_Q),
            "enabled": enabled,
        })
    return validated


FACTORY_DSP_PRESETS = [
    {
        "id": 20,
        "name": "Reference Neutral",
        "description": "A restrained balancing curve for clearer reference listening without obvious coloration.",
        "preset_key": "reference_neutral",
        "category": "reference",
        "is_factory": 1,
        "preamp_db": -2.0,
        "filter_chain_json": _json_text([
            {"type": "low_shelf", "freq": 90, "gain": -0.5, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 250, "gain": -0.7, "q": 0.9, "enabled": True},
            {"type": "peaking", "freq": 2500, "gain": 0.5, "q": 0.9, "enabled": True},
            {"type": "high_shelf", "freq": 10000, "gain": 0.4, "q": 0.7, "enabled": True},
        ]),
        "tags": _json_text(["reference", "neutral", "balanced"]),
        "sort_order": 5,
    },
    {
        "id": 2,
        "name": "Black Background",
        "description": "Trims bass bloom and low-mid haze for a darker, quieter backdrop.",
        "preset_key": "black_background",
        "category": "taste",
        "is_factory": 1,
        "preamp_db": -4.0,
        "filter_chain_json": _json_text([
            {"type": "low_shelf", "freq": 90, "gain": -0.8, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 180, "gain": -1.5, "q": 0.9, "enabled": True},
            {"type": "peaking", "freq": 320, "gain": -1.2, "q": 1.0, "enabled": True},
            {"type": "peaking", "freq": 2400, "gain": 0.8, "q": 0.8, "enabled": True},
            {"type": "high_shelf", "freq": 11000, "gain": 0.7, "q": 0.7, "enabled": True},
        ]),
        "tags": _json_text(["dark", "clean", "low-mid-trim"]),
        "sort_order": 10,
    },
    {
        "id": 3,
        "name": "Vocal Forward Warm",
        "description": "Pulls vocals closer while keeping tone smooth and full-bodied.",
        "preset_key": "vocal_forward_warm",
        "category": "taste",
        "is_factory": 1,
        "preamp_db": -3.0,
        "filter_chain_json": _json_text([
            {"type": "peaking", "freq": 220, "gain": -1.0, "q": 0.9, "enabled": True},
            {"type": "peaking", "freq": 1800, "gain": 1.5, "q": 0.9, "enabled": True},
            {"type": "peaking", "freq": 2800, "gain": 1.0, "q": 1.0, "enabled": True},
            {"type": "high_shelf", "freq": 9000, "gain": -0.5, "q": 0.7, "enabled": True},
        ]),
        "tags": _json_text(["vocals", "warm", "smooth"]),
        "sort_order": 20,
    },
    {
        "id": 4,
        "name": "Vocal Forward Cool",
        "description": "Adds articulation and air for clearer, cooler vocal outlines.",
        "preset_key": "vocal_forward_cool",
        "category": "taste",
        "is_factory": 1,
        "preamp_db": -3.5,
        "filter_chain_json": _json_text([
            {"type": "peaking", "freq": 220, "gain": -1.2, "q": 0.9, "enabled": True},
            {"type": "peaking", "freq": 2200, "gain": 1.8, "q": 1.0, "enabled": True},
            {"type": "peaking", "freq": 4800, "gain": 1.2, "q": 1.2, "enabled": True},
            {"type": "high_shelf", "freq": 10000, "gain": 0.8, "q": 0.7, "enabled": True},
        ]),
        "tags": _json_text(["vocals", "cool", "air"]),
        "sort_order": 30,
    },
    {
        "id": 5,
        "name": "Bass Boost",
        "description": "Adds extra low-end weight while keeping the mids mostly intact.",
        "preset_key": "bass_boost",
        "category": "taste",
        "is_factory": 1,
        "preamp_db": -5.0,
        "filter_chain_json": _json_text([
            {"type": "low_shelf", "freq": 85, "gain": 2.8, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 180, "gain": 0.8, "q": 1.0, "enabled": True},
        ]),
        "tags": _json_text(["bass", "fun", "impact"]),
        "sort_order": 40,
    },
    {
        "id": 21,
        "name": "Sub Bass Boost",
        "description": "Lifts the deepest bass while trimming upper-bass bloom to keep the mix clean.",
        "preset_key": "sub_bass_boost",
        "category": "taste",
        "is_factory": 1,
        "preamp_db": -5.5,
        "filter_chain_json": _json_text([
            {"type": "low_shelf", "freq": 55, "gain": 3.2, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 140, "gain": -0.6, "q": 1.0, "enabled": True},
        ]),
        "tags": _json_text(["bass", "sub", "clean"]),
        "sort_order": 45,
    },
    {
        "id": 6,
        "name": "Pop",
        "description": "Punchier bass, brighter energy, and stronger vocal focus for modern mixes.",
        "preset_key": "pop",
        "category": "genre",
        "is_factory": 1,
        "preamp_db": -4.0,
        "filter_chain_json": _json_text([
            {"type": "low_shelf", "freq": 80, "gain": 1.5, "q": 0.7, "enabled": True},
            {"type": "peaking", "freq": 250, "gain": -1.0, "q": 0.9, "enabled": True},
            {"type": "peaking", "freq": 2500, "gain": 1.2, "q": 1.0, "enabled": True},
            {"type": "high_shelf", "freq": 9500, "gain": 1.0, "q": 0.7, "enabled": True},
        ]),
        "tags": _json_text(["genre", "pop", "energetic"]),
        "sort_order": 50,
    },
    {
        "id": 7,
        "name": "Classical",
        "description": "Opens the stage gently while preserving low-fatigue, natural timbre.",
        "preset_key": "classical",
        "category": "genre",
        "is_factory": 1,
        "preamp_db": -2.5,
        "filter_chain_json": _json_text([
            {"type": "peaking", "freq": 160, "gain": -0.5, "q": 0.8, "enabled": True},
            {"type": "peaking", "freq": 3000, "gain": 0.6, "q": 0.8, "enabled": True},
            {"type": "high_shelf", "freq": 10000, "gain": 0.8, "q": 0.7, "enabled": True},
        ]),
        "tags": _json_text(["genre", "classical", "natural"]),
        "sort_order": 60,
    },
]


def legacy_eq_bands_to_filters(eq_bands_raw: Any) -> list[dict[str, Any]]:
    if not eq_bands_raw:
        return []

    eq_bands = eq_bands_raw
    if isinstance(eq_bands_raw, str):
        try:
            eq_bands = json.loads(eq_bands_raw)
        except json.JSONDecodeError:
            return []

    filters: list[dict[str, Any]] = []
    for band in eq_bands or []:
        try:
            filters.append(
                {
                    "type": "peaking",
                    "freq": float(band.get("freq", 1000)),
                    "gain": float(band.get("gain", 0)),
                    "q": float(band.get("q", 1.4)),
                    "enabled": bool(band.get("enabled", True)),
                }
            )
        except (TypeError, ValueError, AttributeError):
            continue
    return filters


def sanitize_dsp_filters(raw_filters: Any) -> list[dict[str, Any]]:
    """Lenient reader for already-stored filters (legacy rows are clamped).

    User input goes through validate_dsp_filters instead. Non-finite values are
    dropped here: clamping NaN would silently turn it into a maximum value.
    """
    if not raw_filters:
        return []

    filters = raw_filters
    if isinstance(raw_filters, str):
        try:
            filters = json.loads(raw_filters)
        except json.JSONDecodeError:
            return []

    if not isinstance(filters, list):
        return []

    normalized_filters: list[dict[str, Any]] = []
    for flt in filters:
        if not isinstance(flt, dict):
            continue

        filter_type = str(flt.get("type", "peaking")).lower()
        if filter_type not in VALID_FILTER_TYPES:
            continue

        freq = _finite_float(flt.get("freq", 1000))
        gain = _finite_float(flt.get("gain", 0))
        q = _finite_float(flt.get("q", 1.0))
        if freq is None or gain is None or q is None:
            continue

        normalized_filters.append(
            {
                "type": filter_type,
                "freq": _clamp(freq, MIN_FILTER_FREQ, MAX_FILTER_FREQ),
                "gain": _clamp(gain, MIN_FILTER_GAIN, MAX_FILTER_GAIN),
                "q": _clamp(q, MIN_FILTER_Q, MAX_FILTER_Q),
                "enabled": bool(flt.get("enabled", True)),
            }
        )

    return normalized_filters


def profile_filters(dsp_profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Filters of a profile. An explicit empty chain ('[]') is an intentional
    bypass; only a missing chain (NULL/absent) falls back to legacy eq_bands."""
    if not dsp_profile:
        return []

    raw_filters = dsp_profile.get("filter_chain_json")
    if raw_filters is not None and raw_filters != "":
        return sanitize_dsp_filters(raw_filters)

    return legacy_eq_bands_to_filters(dsp_profile.get("eq_bands"))


def _biquad_coefficients(flt: dict[str, Any], sample_rate: float) -> tuple[float, ...]:
    """RBJ cookbook biquad matching what FFmpeg actually renders.

    equalizer (t=q) is the plain RBJ peaking filter. FFmpeg's bass/treble
    shelves measure as an RBJ shelf with Q scaled by sqrt(2) (verified against
    FFmpeg impulse responses in tests/test_dsp_engine.py), so w=0.7 is not the
    flat Butterworth-like shelf and overshoots slightly.
    """
    filter_type = flt.get("type", "peaking")
    q = float(flt["q"]) * (math.sqrt(2) if filter_type in ("low_shelf", "high_shelf") else 1)
    a = 10 ** (float(flt["gain"]) / 40)
    w0 = 2 * math.pi * float(flt["freq"]) / sample_rate
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2 * q)
    root = 2 * math.sqrt(a) * alpha
    if filter_type == "low_shelf":
        return (a * ((a + 1) - (a - 1) * cos_w0 + root), 2 * a * ((a - 1) - (a + 1) * cos_w0),
                a * ((a + 1) - (a - 1) * cos_w0 - root),
                (a + 1) + (a - 1) * cos_w0 + root, -2 * ((a - 1) + (a + 1) * cos_w0),
                (a + 1) + (a - 1) * cos_w0 - root)
    if filter_type == "high_shelf":
        return (a * ((a + 1) + (a - 1) * cos_w0 + root), -2 * a * ((a - 1) + (a + 1) * cos_w0),
                a * ((a + 1) + (a - 1) * cos_w0 - root),
                (a + 1) - (a - 1) * cos_w0 + root, 2 * ((a - 1) - (a + 1) * cos_w0),
                (a + 1) - (a - 1) * cos_w0 - root)
    return (1 + alpha * a, -2 * cos_w0, 1 - alpha * a, 1 + alpha / a, -2 * cos_w0, 1 - alpha / a)


def _active_coefficients(filters: list[dict[str, Any]], sample_rate: float) -> list[tuple[float, ...]]:
    return [_biquad_coefficients(f, sample_rate) for f in filters
            if f.get("enabled", True) and f.get("gain")]


def _cascade_db(coefficients: list[tuple[float, ...]], sample_rate: float, freq: float) -> float:
    z1 = cmath.exp(-2j * math.pi * freq / sample_rate)
    z2 = z1 * z1
    total = 1 + 0j
    for b0, b1, b2, a0, a1, a2 in coefficients:
        total *= (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
    return 20 * math.log10(max(abs(total), 1e-12))


def filters_response_db(filters: list[dict[str, Any]], sample_rate: float,
                        freqs: list[float]) -> list[float]:
    """Combined magnitude response (dB) of the enabled filters, cascaded."""
    coefficients = _active_coefficients(filters, sample_rate)
    return [_cascade_db(coefficients, sample_rate, freq) for freq in freqs]


def _biquad_extremum_freqs(coefficients: tuple[float, ...], sample_rate: float) -> list[float]:
    """Exact frequencies where one biquad's |H| has a stationary point.

    |H|^2 = N(c)/D(c) with c = cos(w) and N, D quadratic in c, so d/dc = 0 is a
    quadratic. Seeding the search grid with these puts a sample at the centre
    of each resonance (the mirrored grid covers ill-conditioned cases near
    Nyquist, where this quadratic has a near-double root).
    """
    b0, b1, b2, a0, a1, a2 = coefficients

    def power_poly(x0: float, x1: float, x2: float) -> tuple[float, float, float]:
        # |x0 + x1 z + x2 z^2|^2 on the unit circle, as a polynomial in cos(w).
        return x0 * x0 + x1 * x1 + x2 * x2 - 2 * x0 * x2, 2 * (x0 * x1 + x1 * x2), 4 * x0 * x2

    n0, n1, n2 = power_poly(b0, b1, b2)
    d0, d1, d2 = power_poly(a0, a1, a2)
    qa, qb, qc = n2 * d1 - n1 * d2, 2 * (n2 * d0 - n0 * d2), n1 * d0 - n0 * d1
    disc = qb * qb - 4 * qa * qc
    if disc < 0:
        roots = []
    else:
        q = -0.5 * (qb + math.copysign(math.sqrt(disc), qb))
        roots = [r for r in ((q / qa) if qa else None, (qc / q) if q else None) if r is not None]
    return [math.acos(c) * sample_rate / (2 * math.pi) for c in roots if -1 < c < 1]


PEAK_GRID_POINTS = 2048
# The mirrored grid reaches this close to Nyquist.
PEAK_GRID_MIN_DISTANCE_HZ = 0.01
# Only grid local maxima this close to the grid maximum are refined: with >= 14
# grid points per filter bandwidth, refinement gains far less than this.
PEAK_REFINE_WINDOW_DB = 1.0
_GOLDEN = (math.sqrt(5) - 1) / 2


def _peak_search_grid(filters: list[dict[str, Any]], coefficients: list[tuple[float, ...]],
                      sample_rate: float) -> list[float]:
    """Frequencies (Hz) sampled before refinement.

    Log spacing from 1 Hz resolves narrow features near DC; frequency warping
    squeezes resonances near Nyquist just as tightly, so a mirrored grid, log
    spaced in the distance to Nyquist, covers that end. Filter centres and each
    biquad's exact extremum frequencies are added as well.
    """
    nyquist = sample_rate / 2
    span = math.log(nyquist / PEAK_GRID_MIN_DISTANCE_HZ)
    points = {math.exp(math.log(nyquist) * i / (PEAK_GRID_POINTS - 1)) for i in range(PEAK_GRID_POINTS)}
    points |= {nyquist - PEAK_GRID_MIN_DISTANCE_HZ * math.exp(span * i / (PEAK_GRID_POINTS // 2 - 1))
               for i in range(PEAK_GRID_POINTS // 2)}
    points |= {float(f["freq"]) for f in filters}
    points |= {f for c in coefficients for f in _biquad_extremum_freqs(c, sample_rate)}
    return sorted(f for f in points if 0 < f < nyquist)


def peak_gain_db(filters: list[dict[str, Any]], sample_rate: float) -> float:
    """Maximum combined boost from DC to Nyquist (>= 0 dB).

    A fixed log grid alone misses narrow resonances (a Q=10 shelf near Nyquist
    was underestimated by 4 dB). The search samples _peak_search_grid, refines
    local maxima near the grid maximum with a golden-section search between
    their neighbours, and evaluates DC/Nyquist exactly.
    """
    coefficients = _active_coefficients(filters, sample_rate)
    if not coefficients:
        return 0.0

    def response(freq: float) -> float:
        return _cascade_db(coefficients, sample_rate, freq)

    grid = _peak_search_grid(filters, coefficients, sample_rate)
    values = [response(f) for f in grid]
    grid_max = max(values)
    best = max(grid_max, response(0.0), response(sample_rate / 2))
    for i, value in enumerate(values):
        if value < grid_max - PEAK_REFINE_WINDOW_DB:
            continue
        left, right = values[i - 1] if i else -math.inf, values[i + 1] if i + 1 < len(values) else -math.inf
        if value < left or value < right:
            continue
        a, b = grid[i - 1] if i else 0.0, grid[i + 1] if i + 1 < len(grid) else sample_rate / 2
        c, d = b - _GOLDEN * (b - a), a + _GOLDEN * (b - a)
        fc, fd = response(c), response(d)
        for _ in range(40):
            if fc > fd:
                b, d, fd = d, c, fc
                c = b - _GOLDEN * (b - a)
                fc = response(c)
            else:
                a, c, fc = c, d, fd
                d = a + _GOLDEN * (b - a)
                fd = response(d)
        best = max(best, fc, fd)
    return max(0.0, best)


def recommended_preamp_db(filters: list[dict[str, Any]], sample_rate: float,
                          margin_db: float = DEFAULT_HEADROOM_MARGIN_DB) -> float:
    """Attenuation keeping the combined EQ peak plus a safety margin below 0 dBFS.

    Never capped: overlapping +12 dB boosts need more than -12 dB.
    """
    return _preamp_for_peak_db(peak_gain_db(filters, sample_rate), margin_db)


# Source-rate families VELVET may run DSP at (the EQ runs at the source rate,
# before any resampling). Peaks near the top of the band grow with the rate:
# a GEQ curve peaking at +15.2 dB at 48 kHz reaches +17.0 dB at 192 kHz.
DSP_REFERENCE_SAMPLE_RATES = (22050, 32000, 44100, 48000, 88200, 96000,
                              176400, 192000, 352800, 384000, 705600, 768000)


def _playable_filters(filters: list[dict[str, Any]], sample_rate: float) -> list[dict[str, Any]]:
    return [f for f in filters if float(f["freq"]) < sample_rate / 2]


def _preamp_for_peak_db(peak: float, margin_db: float = DEFAULT_HEADROOM_MARGIN_DB) -> float:
    if peak <= 0.05:
        return 0.0
    return -math.ceil((peak + margin_db) * 10 - 1e-9) / 10


def _worst_case_peak_db(filters: list[dict[str, Any]]) -> float:
    return max(peak_gain_db(_playable_filters(filters, rate), rate) for rate in DSP_REFERENCE_SAMPLE_RATES)


def worst_case_preamp_db(filters: list[dict[str, Any]]) -> float:
    """Preamp that keeps headroom at every reference source rate."""
    return _preamp_for_peak_db(_worst_case_peak_db(filters))


def validate_dsp_headroom(filters: list[dict[str, Any]]) -> None:
    """Fail-safe policy: reject a filter set whose combined boost cannot be
    offset by the largest allowed preamp attenuation (plus safety margin)."""
    for rate in DSP_REFERENCE_SAMPLE_RATES:
        playable = _playable_filters(filters, rate)
        needed = recommended_preamp_db(playable, rate)
        if needed < MIN_PREAMP_DB:
            raise DspValidationError(
                f"combined EQ boost peaks at +{peak_gain_db(playable, rate):.1f} dB at {rate} Hz; "
                f"keeping headroom would need {needed:.1f} dB preamp, beyond the "
                f"{MIN_PREAMP_DB:g} dB limit. Reduce the boosts or Q.")


def _source_rate(sample_rate: Any) -> float | None:
    rate = _finite_float(sample_rate)
    return rate if rate and rate > 0 else None


def _active_filters(dsp_profile: dict[str, Any] | None,
                    sample_rate: float | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(filters to apply, filters disabled at this sample rate with a reason)."""
    applied: list[dict[str, Any]] = []
    disabled: list[dict[str, Any]] = []
    for flt in profile_filters(dsp_profile):
        if not flt.get("enabled", True) or not flt.get("gain"):
            continue
        if sample_rate and flt["freq"] >= sample_rate / 2:
            disabled.append({**flt, "reason": f"{flt['freq']:g} Hz is at or above the Nyquist "
                                              f"frequency of {sample_rate:g} Hz audio"})
            continue
        applied.append(flt)
    return applied, disabled


def _guarded_processing(profile: dict[str, Any], rate: float | None) -> dict[str, Any]:
    """Filters and preamp actually applied at a source rate.

    Headroom guard: when the saved preamp leaves less than
    HEADROOM_GUARD_MIN_DB below full scale at this rate, the recommended
    preamp (with the full safety margin) is applied instead. An unknown rate
    uses the worst case over DSP_REFERENCE_SAMPLE_RATES.
    """
    applied, disabled = _active_filters(profile, rate)
    preamp_db = _finite_float(profile.get("preamp_db", 0) or 0) or 0.0
    if rate:
        peak = peak_gain_db(applied, rate)
        needed = recommended_preamp_db(applied, rate)
    else:
        peak = _worst_case_peak_db(applied)
        needed = _preamp_for_peak_db(peak)
    insufficient = peak + preamp_db > -HEADROOM_GUARD_MIN_DB and (peak > 0.05 or preamp_db > 0)
    effective = min(preamp_db, needed) if insufficient else preamp_db
    return {"applied": applied, "disabled": disabled, "preamp_db": preamp_db,
            "effective_preamp_db": effective, "needed_preamp_db": needed, "peak": peak}


def _filter_chain(profile: dict[str, Any], applied: list[dict[str, Any]], preamp_db: float) -> str:
    filters: list[str] = []
    if preamp_db:
        # Default float precision would push the EQ below into float32, which
        # skews resonant low-frequency filters (~0.1 dB) away from the model.
        filters.append(f"volume={preamp_db}dB:precision=double")

    for flt in applied:
        freq, gain, q = flt["freq"], flt["gain"], flt["q"]
        if flt["type"] == "peaking":
            filters.append(f"equalizer=f={freq}:t=q:w={q}:g={gain}")
        elif flt["type"] == "low_shelf":
            filters.append(f"bass=g={gain}:f={freq}:w={q}")
        elif flt["type"] == "high_shelf":
            filters.append(f"treble=g={gain}:f={freq}:w={q}")

    if profile.get("volume_normalize"):
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    stereo_width = _finite_float(profile.get("stereo_width", 0) or 0)
    if stereo_width:
        filters.append(f"stereotools=phase=0:slev={1 + stereo_width/100}")

    room_size = _finite_float(profile.get("reverb_room_size", 0) or 0)
    wet_dry = _finite_float(profile.get("reverb_wet_dry", 0) or 0)
    if room_size and wet_dry:
        filters.append(f"aecho=0.8:0.9:{room_size/100}:{wet_dry/100}")

    return ",".join(filters)


def resolve_dsp_plan(dsp_profile: dict[str, Any] | None, sample_rate: Any = None) -> dict[str, Any]:
    """What will actually be applied, plus a combined-response headroom estimate."""
    profile = dsp_profile or {}
    rate = _source_rate(sample_rate)
    processing = _guarded_processing(profile, rate)
    effective = processing["effective_preamp_db"]
    net_peak = processing["peak"] + effective
    return {
        "filters": processing["applied"],
        "disabled_filters": processing["disabled"],
        "preamp_db": processing["preamp_db"],
        "effective_preamp_db": effective,
        "headroom_guard_db": round(effective - processing["preamp_db"], 2),
        "sample_rate": rate,
        "headroom_rate_basis": "source sample rate" if rate else "worst case of reference sample rates",
        "peak_gain_db": round(processing["peak"], 2),
        "net_peak_db": round(net_peak, 2),
        "eq_clipping_risk": net_peak > 0.05,
        "estimate_scope": ("EQ frequency-response estimate for full-scale input after the headroom "
                           "guard; not a post-DSP sample or true-peak measurement, and excludes "
                           "level_changing_effects"),
        "recommended_preamp_db": processing["needed_preamp_db"],
        "active": bool(_filter_chain(profile, processing["applied"], effective)),
        # The headroom estimate covers the EQ stage; these stages change level too.
        "level_changing_effects": [name for name, on in (
            ("loudnorm", profile.get("volume_normalize")),
            ("stereo_width", _finite_float(profile.get("stereo_width", 0) or 0)),
            ("reverb", _finite_float(profile.get("reverb_wet_dry", 0) or 0)),
        ) if on],
    }


def build_dsp_filter_chain(dsp_profile: dict[str, Any] | None, sample_rate: Any = None) -> str:
    if not dsp_profile:
        return ""
    processing = _guarded_processing(dsp_profile, _source_rate(sample_rate))
    return _filter_chain(dsp_profile, processing["applied"], processing["effective_preamp_db"])


def dsp_effective_status(requested_profile_id: Any, dsp_profile: dict[str, Any] | None,
                         sample_rate: Any = None) -> dict[str, Any]:
    """Requested vs effective DSP on an output path that applies VELVET DSP."""
    if not requested_profile_id:
        return {"requested_profile_id": None, "effective_profile_id": None,
                "status": "off", "reason": "VELVET DSP off", "disabled_filters": []}
    plan = resolve_dsp_plan(dsp_profile, sample_rate)
    if not plan["active"]:
        # Empty processing graph: the output is the source, whatever is selected.
        return {"requested_profile_id": requested_profile_id, "effective_profile_id": None,
                "status": "bypassed",
                "reason": "Preset has no active processing at this sample rate (source direct)",
                "disabled_filters": plan["disabled_filters"]}
    return {"requested_profile_id": requested_profile_id,
            "effective_profile_id": requested_profile_id,
            "status": "applied", "reason": None,
            "disabled_filters": plan["disabled_filters"],
            "headroom_guard_db": plan["headroom_guard_db"],
            "net_peak_db": plan["net_peak_db"], "eq_clipping_risk": plan["eq_clipping_risk"]}
