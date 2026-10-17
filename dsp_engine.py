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
# Response/headroom estimates assume this rate when the source rate is unknown.
DEFAULT_ANALYSIS_SAMPLE_RATE = 48000


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


def filters_response_db(filters: list[dict[str, Any]], sample_rate: float,
                        freqs: list[float]) -> list[float]:
    """Combined magnitude response (dB) of the enabled filters, cascaded."""
    active = [_biquad_coefficients(f, sample_rate) for f in filters
              if f.get("enabled", True) and f.get("gain")]
    response: list[float] = []
    for freq in freqs:
        z1 = cmath.exp(-2j * math.pi * freq / sample_rate)
        z2 = z1 * z1
        total = 1 + 0j
        for b0, b1, b2, a0, a1, a2 in active:
            total *= (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
        response.append(20 * math.log10(max(abs(total), 1e-12)))
    return response


def _analysis_grid(filters: list[dict[str, Any]], sample_rate: float, points: int = 1024) -> list[float]:
    low, high = 5.0, sample_rate / 2 * 0.999
    ratio = (high / low) ** (1 / (points - 1))
    grid = [low * ratio ** i for i in range(points)]
    grid.extend(float(f["freq"]) for f in filters if float(f["freq"]) < high)
    return grid


def peak_gain_db(filters: list[dict[str, Any]], sample_rate: float) -> float:
    """Maximum combined boost on a dense log grid up to Nyquist (>= 0 dB)."""
    if not any(f.get("enabled", True) and f.get("gain") for f in filters):
        return 0.0
    return max(0.0, max(filters_response_db(filters, sample_rate, _analysis_grid(filters, sample_rate))))


def recommended_preamp_db(filters: list[dict[str, Any]], sample_rate: float,
                          margin_db: float = DEFAULT_HEADROOM_MARGIN_DB) -> float:
    """Attenuation keeping the combined EQ peak plus a safety margin below 0 dBFS.

    Never capped: overlapping +12 dB boosts need more than -12 dB.
    """
    peak = peak_gain_db(filters, sample_rate)
    if peak <= 0.05:
        return 0.0
    return -math.ceil((peak + margin_db) * 10 - 1e-9) / 10


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


def resolve_dsp_plan(dsp_profile: dict[str, Any] | None, sample_rate: Any = None) -> dict[str, Any]:
    """What will actually be applied, plus a combined-response headroom estimate."""
    profile = dsp_profile or {}
    rate = _source_rate(sample_rate)
    analysis_rate = rate or DEFAULT_ANALYSIS_SAMPLE_RATE
    applied, disabled = _active_filters(profile, rate)
    preamp_db = _finite_float(profile.get("preamp_db", 0) or 0) or 0.0
    peak = peak_gain_db(applied, analysis_rate)
    net_peak = peak + preamp_db
    return {
        "filters": applied,
        "disabled_filters": disabled,
        "preamp_db": preamp_db,
        "sample_rate": analysis_rate,
        "sample_rate_assumed": rate is None,
        "peak_gain_db": round(peak, 2),
        "net_peak_db": round(net_peak, 2),
        "clipping_risk": net_peak > 0.05,
        "recommended_preamp_db": recommended_preamp_db(applied, analysis_rate),
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

    filters: list[str] = []

    preamp_db = _finite_float(dsp_profile.get("preamp_db", 0) or 0) or 0.0
    if preamp_db:
        filters.append(f"volume={preamp_db}dB")

    for flt in _active_filters(dsp_profile, _source_rate(sample_rate))[0]:
        freq, gain, q = flt["freq"], flt["gain"], flt["q"]
        if flt["type"] == "peaking":
            filters.append(f"equalizer=f={freq}:t=q:w={q}:g={gain}")
        elif flt["type"] == "low_shelf":
            filters.append(f"bass=g={gain}:f={freq}:w={q}")
        elif flt["type"] == "high_shelf":
            filters.append(f"treble=g={gain}:f={freq}:w={q}")

    if dsp_profile.get("volume_normalize"):
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    stereo_width = _finite_float(dsp_profile.get("stereo_width", 0) or 0)
    if stereo_width:
        filters.append(f"stereotools=phase=0:slev={1 + stereo_width/100}")

    room_size = _finite_float(dsp_profile.get("reverb_room_size", 0) or 0)
    wet_dry = _finite_float(dsp_profile.get("reverb_wet_dry", 0) or 0)
    if room_size and wet_dry:
        filters.append(f"aecho=0.8:0.9:{room_size/100}:{wet_dry/100}")

    return ",".join(filters)


def dsp_effective_status(requested_profile_id: Any, dsp_profile: dict[str, Any] | None,
                         sample_rate: Any = None) -> dict[str, Any]:
    """Requested vs effective DSP on an output path that applies VELVET DSP."""
    if not requested_profile_id:
        return {"requested_profile_id": None, "effective_profile_id": None,
                "status": "off", "reason": "VELVET DSP off", "disabled_filters": []}
    plan = resolve_dsp_plan(dsp_profile, sample_rate)
    return {"requested_profile_id": requested_profile_id,
            "effective_profile_id": requested_profile_id,
            "status": "applied", "reason": None,
            "disabled_filters": plan["disabled_filters"],
            "net_peak_db": plan["net_peak_db"], "clipping_risk": plan["clipping_risk"]}
