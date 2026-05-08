import json
from typing import Any


VALID_FILTER_TYPES = {"peaking", "low_shelf", "high_shelf"}
MIN_FILTER_FREQ = 20.0
MAX_FILTER_FREQ = 20000.0
MIN_FILTER_GAIN = -18.0
MAX_FILTER_GAIN = 18.0
MIN_FILTER_Q = 0.1
MAX_FILTER_Q = 10.0


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


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

        try:
            freq = _clamp(float(flt.get("freq", 1000)), MIN_FILTER_FREQ, MAX_FILTER_FREQ)
            gain = _clamp(float(flt.get("gain", 0)), MIN_FILTER_GAIN, MAX_FILTER_GAIN)
            q = _clamp(float(flt.get("q", 1.0)), MIN_FILTER_Q, MAX_FILTER_Q)
        except (TypeError, ValueError):
            continue

        normalized_filters.append(
            {
                "type": filter_type,
                "freq": freq,
                "gain": gain,
                "q": q,
                "enabled": bool(flt.get("enabled", True)),
            }
        )

    return normalized_filters


def profile_filters(dsp_profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not dsp_profile:
        return []

    raw_filters = dsp_profile.get("filter_chain_json")
    if raw_filters:
        sanitized = sanitize_dsp_filters(raw_filters)
        if sanitized:
            return sanitized

    return legacy_eq_bands_to_filters(dsp_profile.get("eq_bands"))


def build_dsp_filter_chain(dsp_profile: dict[str, Any] | None) -> str:
    if not dsp_profile:
        return ""

    filters: list[str] = []

    try:
        preamp_db = float(dsp_profile.get("preamp_db", 0) or 0)
    except (TypeError, ValueError):
        preamp_db = 0.0
    if preamp_db:
        filters.append(f"volume={preamp_db}dB")

    for flt in profile_filters(dsp_profile):
        try:
            if not flt.get("enabled", True):
                continue

            filter_type = str(flt.get("type", "peaking")).lower()
            freq = float(flt.get("freq", 1000))
            gain = float(flt.get("gain", 0))
            q = float(flt.get("q", 1.0))

            if gain == 0:
                continue

            if filter_type == "peaking":
                filters.append(f"equalizer=f={freq}:t=q:w={q}:g={gain}")
            elif filter_type == "low_shelf":
                filters.append(f"bass=g={gain}:f={freq}:w={q}")
            elif filter_type == "high_shelf":
                filters.append(f"treble=g={gain}:f={freq}:w={q}")
        except (TypeError, ValueError, AttributeError):
            continue

    if dsp_profile.get("volume_normalize"):
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    stereo_width = dsp_profile.get("stereo_width", 0)
    if stereo_width:
        filters.append(f"stereotools=phase=0:width={1 + float(stereo_width)/100}")

    room_size = dsp_profile.get("reverb_room_size", 0)
    wet_dry = dsp_profile.get("reverb_wet_dry", 0)
    if room_size and wet_dry:
        filters.append(f"aecho=0.8:0.9:{float(room_size)/100}:{float(wet_dry)/100}")

    return ",".join(filters)
