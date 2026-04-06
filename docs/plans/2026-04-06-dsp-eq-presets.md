# DSP EQ Presets Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Roon-inspired DSP/EQ system for VELVET with factory presets, user custom EQ, and a clean preset taxonomy for different listening intents like black background, vocal-forward warm/cool, bass boost, pop, and classical.

**Architecture:** Reuse the existing `dsp_profiles` + FFmpeg filter-chain path as the core DSP engine, but split the product into three layers: playback safety, reference/correction, and taste/scene presets. Unify browser streaming, cast streaming, and native local playback so they all resolve the same DSP profile model and apply the same validated filter graph, with headroom protection enabled by design.

**Tech Stack:** FastAPI, SQLite migrations, FFmpeg audio filters, `sounddevice` local playback, single-file frontend in `static/index.html`, unittest + FastAPI `TestClient`.

---

## Context Summary

### Existing codebase facts

- DSP profiles already exist in [db_schema.py](C:\Antigravity\Claude Code\VELVET_NEXT\db_schema.py).
- DSP CRUD and FFmpeg filter-chain generation already exist in [server.py](C:\Antigravity\Claude Code\VELVET_NEXT\server.py).
- Frontend DSP selector already exists in [static/index.html](C:\Antigravity\Claude Code\VELVET_NEXT\static/index.html).
- Native playback currently uses [local_playback.py](C:\Antigravity\Claude Code\VELVET_NEXT\local_playback.py), but this path is not yet aligned with the server-side DSP pipeline.

### Important risks to address before feature expansion

1. Native playback currently bypasses real DSP preset application.
   `playTrack()` in [static/index.html](C:\Antigravity\Claude Code\VELVET_NEXT\static/index.html) sends `track_id`, `device_id`, `latency`, and `volume` to `/api/playback/play`, but not the selected DSP profile or upsample mode.

2. Native playback sample rate handling looks inconsistent.
   [local_playback.py](C:\Antigravity\Claude Code\VELVET_NEXT\local_playback.py) asks FFmpeg for `44100` Hz PCM while the `sounddevice.OutputStream` is created at `48000` Hz. Fix this before claiming trustworthy DSP behavior.

3. Current DSP data model is too shallow for a serious preset system.
   The current table stores `eq_bands`, normalization, stereo width, and pseudo-reverb, but it has no concept of preset category, preamp/headroom, source/reference origin, device scope, filter type, or import/export metadata.

---

## Product Direction

### DSP should be split into 3 layers

1. **Safety Layer**
   Always-on or strongly recommended utilities that prevent bad sound rather than “shape” sound.
   Includes:
   - Headroom / preamp reduction
   - Clipping indicator
   - Loudness normalization toggle
   - Output/sample-rate policy

2. **Reference / Correction Layer**
   Measurement-driven correction profiles for headphones or target curves.
   Reference sources to design around:
   - Roon MUSE Parametric EQ / Procedural EQ / Headroom model
   - AutoEq
   - oratory1990 presets
   - Hangout.Audio / Squig-style graph-EQ workflow

3. **Taste / Scene Layer**
   Musical voicing presets for different listening preferences or genres.
   This is where your requested “black background”, “vocals warm/cool”, “bass boost”, “pop”, and “classical” should live.

### Preset families to ship first

#### A. Reference

- `Flat`
  No processing except optional safety headroom.

- `Reference Neutral`
  Mild balancing curve. Small bass containment, slight upper-mid cleanup, minimal coloration.

- `Studio Monitor`
  Similar to `Reference Neutral`, but slightly tighter low end and slightly more presence definition.

#### B. Black / Quiet Background family

- `Black Background`
  Goal: cleaner, darker, less hazy stage.
  Tonal intent:
  - Trim mid-bass bloom around 120–250 Hz
  - Reduce low-mid thickness around 250–500 Hz
  - Keep upper mids controlled, avoid shout
  - Add very light air only if needed

- `Black Background+`
  Stronger version for already warm systems.
  Tonal intent:
  - More aggressive mud reduction
  - Tighter bass edges
  - Slighter leaner tone overall

#### C. Vocal Focus family

- `Vocal Forward Warm`
  Goal: bring singers closer without making them sharp.
  Tonal intent:
  - Slight cut in mid-bass masking
  - Gentle lift around 1.5–3 kHz
  - Soft top-end, no aggressive 6–8 kHz rise

- `Vocal Forward Cool`
  Goal: cleaner articulation, more outline and “air”.
  Tonal intent:
  - Same vocal presence push
  - Less mid-bass warmth
  - Slight extra energy around 4–8 kHz

- `Intimate Vocal`
  Good for jazz, ballads, acoustic singer-songwriter.

#### D. Bass family

- `Bass Boost`
  Fun shelf below ~100 Hz, keep mids mostly intact.

- `Sub Bass Boost`
  Focus the boost below ~70 Hz to avoid muddying kick and lower mids.

- `Bass Tight`
  Less quantity, more punch. Useful for systems with too much bloom.

#### E. Genre family

- `Pop`
  Goal: punch, sparkle, vocal clarity.
  Tonal intent:
  - Slight sub/mid-bass impact
  - Controlled low mids
  - Vocal presence forward
  - Slight high-frequency excitement

- `Classical`
  Goal: stage openness, timbral naturalness, low fatigue.
  Tonal intent:
  - Minimal bass exaggeration
  - Preserve midrange body
  - Gentle treble openness, not glare
  - Wider image only in subtle amounts

- `Jazz`
  Slight warmth, intimate mids, restrained treble edge.

- `Electronic`
  Sub-bass lift, crisp top, mild V-shape.

#### F. User family

- `Custom 10-Band`
- `Custom Parametric`
- `Duplicate From Factory Preset`

### Recommended initial shipping matrix

Ship these as v1 factory presets:

- `Flat`
- `Reference Neutral`
- `Black Background`
- `Vocal Forward Warm`
- `Vocal Forward Cool`
- `Bass Boost`
- `Sub Bass Boost`
- `Pop`
- `Classical`

Hold these for v1.1:

- `Black Background+`
- `Studio Monitor`
- `Jazz`
- `Electronic`
- `Bass Tight`
- `Intimate Vocal`

---

## Reference Notes

### Roon ideas worth copying

- Roon MUSE separates **Headroom Management**, **Sample Rate Conversion**, **Parametric EQ**, **Procedural EQ**, **Convolution**, and vendor presets into explicit blocks rather than hiding everything in one “EQ” page.
- Roon repeatedly emphasizes headroom and clipping prevention. This should become part of VELVET’s default preset design, not an advanced-only setting.
- Roon’s Procedural EQ supports ordered operations. VELVET does not need full parity in v1, but should move from “single JSON field of bands” toward an ordered filter model.
- Roon treats vendor or measurement presets as calibration-style building blocks, then lets the user customize them.

### Famous EQ reference ecosystems worth supporting

- **AutoEq**: best first integration target for measurement-driven import/export and device correction.
- **oratory1990**: good source for hand-tuned measurement-based presets; useful as a “trusted reference” profile family.
- **Hangout.Audio / Squig-style graph tools**: valuable for manual target matching and visual EQ workflows.

### Design conclusion from these references

- VELVET should not mix headphone correction presets and musical taste presets into one undifferentiated list.
- The UI must expose preset **category** and **intent**, or users will not understand why `AutoEq HD650` and `Pop` are fundamentally different things.

---

## Data Model Proposal

### Recommended table evolution

Prefer adding a new migration in [db_schema.py](C:\Antigravity\Claude Code\VELVET_NEXT\db_schema.py) instead of replacing the current table in-place blindly.

### New/expanded fields for `dsp_profiles`

- `category TEXT NOT NULL DEFAULT 'custom'`
  Values:
  - `reference`
  - `correction`
  - `taste`
  - `genre`
  - `custom`

- `preset_key TEXT`
  Stable machine key like `black_background`, `vocal_warm`, `classical`.

- `is_factory INTEGER DEFAULT 0`
- `parent_profile_id INTEGER`
  So a user preset can be cloned from a factory preset.

- `preamp_db REAL DEFAULT 0`
  Explicit headroom/preamp control. This is mandatory.

- `filter_chain_json TEXT`
  Ordered filter objects. Keep `eq_bands` temporarily for backward compatibility during migration.

- `source_reference TEXT`
  Example: `autoeq`, `oratory1990`, `manual`, `velvet_factory`.

- `target_device TEXT`
  Optional headphone/device name for correction presets.

- `tags TEXT`
  JSON list like `["vocal","warm","low-fatigue"]`

- `sort_order INTEGER DEFAULT 0`

### Ordered filter object shape

```json
[
  {
    "type": "peaking",
    "freq": 180,
    "gain": -1.5,
    "q": 0.9,
    "enabled": true
  },
  {
    "type": "low_shelf",
    "freq": 70,
    "gain": 2.5,
    "q": 0.7,
    "enabled": true
  }
]
```

This unlocks:

- Peaking filters
- Low shelf / high shelf
- Future notch / low-pass / high-pass
- Better compatibility with AutoEq-style imports

---

## Backend Plan

### Task 1: Stabilize the DSP domain model

**Files:**
- Modify: [db_schema.py](C:\Antigravity\Claude Code\VELVET_NEXT\db_schema.py)
- Test: [tests/test_db_schema.py](C:\Antigravity\Claude Code\VELVET_NEXT\tests\test_db_schema.py)

**Step 1: Write the failing migration test**

Add a test that applies migrations and asserts the evolved `dsp_profiles` schema contains:

```python
required_columns = {
    "category",
    "preset_key",
    "is_factory",
    "parent_profile_id",
    "preamp_db",
    "filter_chain_json",
    "source_reference",
    "target_device",
    "tags",
    "sort_order",
}
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_db_schema.py -q
```

Expected:
- Fails because the new columns do not exist yet.

**Step 3: Add a schema migration**

Implement a migration that:

- Adds the new columns
- Backfills `category='reference'` and `preset_key='flat'` for profile `id=1`
- Copies legacy `eq_bands` into `filter_chain_json` when possible

**Step 4: Seed factory presets**

Add seed rows for:

```text
Flat
Reference Neutral
Black Background
Vocal Forward Warm
Vocal Forward Cool
Bass Boost
Sub Bass Boost
Pop
Classical
```

**Step 5: Run test to verify it passes**

Run:

```bash
python -m pytest tests/test_db_schema.py -q
```

**Step 6: Commit**

```bash
git add db_schema.py tests/test_db_schema.py
git commit -m "feat: extend dsp profile schema for presets"
```

### Task 2: Replace the legacy EQ-band builder with ordered filter translation

**Files:**
- Modify: [server.py](C:\Antigravity\Claude Code\VELVET_NEXT\server.py)
- Test: [tests/test_server_api.py](C:\Antigravity\Claude Code\VELVET_NEXT\tests\test_server_api.py)

**Step 1: Write failing unit tests for filter-chain generation**

Add tests for:

- peaking filter generation
- low shelf generation
- high shelf generation
- preamp/headroom insertion
- disabled filter omission
- stable filter ordering

**Step 2: Run targeted tests**

Run:

```bash
python -m pytest tests/test_server_api.py -q
```

Expected:
- Fails because `_build_dsp_filter_chain()` only understands the old `eq_bands` shape.

**Step 3: Add a new translator**

Implement a helper with logic similar to:

```python
def _build_filter_steps(profile: dict) -> list[str]:
    steps = []
    if profile.get("preamp_db", 0):
        steps.append(f"volume={profile['preamp_db']}dB")
    for flt in json.loads(profile.get("filter_chain_json") or "[]"):
        if not flt.get("enabled", True):
            continue
        # map peaking / low_shelf / high_shelf to ffmpeg syntax
    if profile.get("volume_normalize"):
        steps.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return steps
```

**Step 4: Keep backward compatibility**

If `filter_chain_json` is empty, continue reading `eq_bands` so old data still works.

**Step 5: Run targeted tests again**

Run:

```bash
python -m pytest tests/test_server_api.py -q
```

**Step 6: Commit**

```bash
git add server.py tests/test_server_api.py
git commit -m "feat: support ordered dsp filters and headroom"
```

### Task 3: Expand DSP APIs for preset taxonomy and cloning

**Files:**
- Modify: [server.py](C:\Antigravity\Claude Code\VELVET_NEXT\server.py)
- Test: [tests/test_server_api.py](C:\Antigravity\Claude Code\VELVET_NEXT\tests\test_server_api.py)

**Step 1: Write failing API tests**

Cover:

- listing grouped presets
- filtering by category
- cloning factory preset into custom preset
- preventing delete of factory preset
- updating only user-editable presets

**Step 2: Add API fields to CRUD responses**

Ensure `/api/dsp/profiles` returns at least:

```json
{
  "id": 2,
  "name": "Black Background",
  "category": "taste",
  "preset_key": "black_background",
  "is_factory": 1,
  "preamp_db": -4.0,
  "tags": ["dark","clean","low-mid-trim"]
}
```

**Step 3: Add clone route**

Recommended route:

```text
POST /api/dsp/profiles/{profile_id}/clone
```

**Step 4: Add import-ready validation**

Reject filters with:

- unsupported type
- invalid freq
- absurd Q
- gain outside safe bounds

**Step 5: Run tests**

```bash
python -m pytest tests/test_server_api.py -q
```

**Step 6: Commit**

```bash
git add server.py tests/test_server_api.py
git commit -m "feat: add dsp preset categories and cloning"
```

### Task 4: Unify native playback with DSP-capable streaming logic

**Files:**
- Modify: [local_playback.py](C:\Antigravity\Claude Code\VELVET_NEXT\local_playback.py)
- Modify: [server.py](C:\Antigravity\Claude Code\VELVET_NEXT\server.py)
- Test: [tests/test_server_api.py](C:\Antigravity\Claude Code\VELVET_NEXT\tests\test_server_api.py)

**Step 1: Write failing test for native playback request payload**

The test should verify `/api/playback/play` accepts:

```json
{
  "track_id": 1,
  "device_id": 3,
  "latency": "medium",
  "volume": 0.85,
  "dsp_profile_id": 4,
  "upsample": "2x"
}
```

**Step 2: Fix `local_playback.py` playback command construction**

Instead of hardcoding a 44.1 kHz decode and a 48 kHz stream, make native playback resolve:

- source sample rate
- target sample rate
- optional upsample mode
- DSP filter chain

Recommended direction:

```python
ffmpeg -i input.flac -af "<dsp_chain>" -f s16le -acodec pcm_s16le -ar <resolved_rate> -ac 2 -
```

**Step 3: Reuse server-side filter generation**

Do not fork DSP logic in two places.
Extract shared helpers if needed so browser/cast/native all call the same builder.

**Step 4: Add safe fallback**

If native mode cannot support the requested DSP/upsample combination, return a structured error instead of silently ignoring the profile.

**Step 5: Run API tests**

```bash
python -m pytest tests/test_server_api.py -q
```

**Step 6: Commit**

```bash
git add local_playback.py server.py tests/test_server_api.py
git commit -m "feat: unify native playback with dsp presets"
```

---

## Frontend Plan

### Task 5: Turn the DSP dropdown into a preset browser

**Files:**
- Modify: [static/index.html](C:\Antigravity\Claude Code\VELVET_NEXT\static\index.html)

**Step 1: Replace the flat dropdown mental model**

Keep the quick dropdown for playback, but add a management surface in Settings:

- Factory Presets
- Reference / Correction
- Genre / Taste
- My Presets

**Step 2: Show preset metadata**

Each preset card should show:

- name
- category
- tonal summary
- recommended use
- headroom/preamp
- tags like `Warm`, `Vocals`, `Dark`, `Classical`

**Step 3: Add A/B controls**

Roon gets this right by making DSP easy to toggle.
Add:

- preset enable/disable
- “compare with Flat”
- “duplicate and edit”

**Step 4: Add custom editor v1**

Start with:

- preamp slider
- filter list
- add peaking / low shelf / high shelf
- per-filter enable toggle
- freq/gain/Q numeric controls

Do not build a full graph editor in v1.

**Step 5: Add tonal descriptions for the launch presets**

Example copy:

```text
Black Background — trims mid-bass bloom and low-mid haze for a quieter, darker canvas.
Vocal Forward Warm — lifts presence while keeping the singer full-bodied and smooth.
Vocal Forward Cool — cleaner vocal edge, more air, leaner warmth.
Pop — tighter bass, brighter energy, stronger vocal presence.
Classical — wider, calmer balance with low fatigue and natural timbre.
```

**Step 6: Manual verification**

Run:

```bash
python server.py
```

Then verify:

- DSP dropdown still works during playback
- Settings page shows grouped presets
- custom preset can be cloned from factory preset

**Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: add grouped dsp preset browser and editor"
```

---

## Preset Tuning Spec

These are starting points, not final “golden curves”. Final values must be tuned by ear and tested on at least:

- a neutral headphone/IEM
- a warm headphone/IEM
- a speaker or speaker-like output path

### `Black Background`

Suggested starting filters:

```json
[
  {"type":"low_shelf","freq":90,"gain":-0.8,"q":0.7,"enabled":true},
  {"type":"peaking","freq":180,"gain":-1.5,"q":0.9,"enabled":true},
  {"type":"peaking","freq":320,"gain":-1.2,"q":1.0,"enabled":true},
  {"type":"peaking","freq":2400,"gain":0.8,"q":0.8,"enabled":true},
  {"type":"high_shelf","freq":11000,"gain":0.7,"q":0.7,"enabled":true}
]
```

Suggested preamp:

```text
-4.0 dB
```

### `Vocal Forward Warm`

```json
[
  {"type":"peaking","freq":220,"gain":-1.0,"q":0.9,"enabled":true},
  {"type":"peaking","freq":1800,"gain":1.5,"q":0.9,"enabled":true},
  {"type":"peaking","freq":2800,"gain":1.0,"q":1.0,"enabled":true},
  {"type":"high_shelf","freq":9000,"gain":-0.5,"q":0.7,"enabled":true}
]
```

Suggested preamp:

```text
-3.0 dB
```

### `Vocal Forward Cool`

```json
[
  {"type":"peaking","freq":220,"gain":-1.2,"q":0.9,"enabled":true},
  {"type":"peaking","freq":2200,"gain":1.8,"q":1.0,"enabled":true},
  {"type":"peaking","freq":4800,"gain":1.2,"q":1.2,"enabled":true},
  {"type":"high_shelf","freq":10000,"gain":0.8,"q":0.7,"enabled":true}
]
```

Suggested preamp:

```text
-3.5 dB
```

### `Bass Boost`

```json
[
  {"type":"low_shelf","freq":85,"gain":2.8,"q":0.7,"enabled":true},
  {"type":"peaking","freq":180,"gain":0.8,"q":1.0,"enabled":true}
]
```

Suggested preamp:

```text
-5.0 dB
```

### `Sub Bass Boost`

```json
[
  {"type":"low_shelf","freq":55,"gain":3.2,"q":0.7,"enabled":true},
  {"type":"peaking","freq":140,"gain":-0.6,"q":1.0,"enabled":true}
]
```

Suggested preamp:

```text
-5.5 dB
```

### `Pop`

```json
[
  {"type":"low_shelf","freq":80,"gain":1.5,"q":0.7,"enabled":true},
  {"type":"peaking","freq":250,"gain":-1.0,"q":0.9,"enabled":true},
  {"type":"peaking","freq":2500,"gain":1.2,"q":1.0,"enabled":true},
  {"type":"high_shelf","freq":9500,"gain":1.0,"q":0.7,"enabled":true}
]
```

Suggested preamp:

```text
-4.0 dB
```

### `Classical`

```json
[
  {"type":"peaking","freq":160,"gain":-0.5,"q":0.8,"enabled":true},
  {"type":"peaking","freq":3000,"gain":0.6,"q":0.8,"enabled":true},
  {"type":"high_shelf","freq":10000,"gain":0.8,"q":0.7,"enabled":true}
]
```

Suggested preamp:

```text
-2.5 dB
```

### Tuning rule

Factory taste presets should stay subtle:

- avoid single-band boosts above `+3.5 dB` for v1
- prefer subtractive cleanup before additive boosts
- always assign negative preamp/headroom when any positive gain exists
- use fewer filters for genre presets than for correction presets

---

## Import Strategy

### v1

- Manual filter entry
- Clone from factory preset
- JSON import/export for VELVET-native format

### v1.1

- Import AutoEq-style parametric filters
- Optional import helpers for oratory1990-style preset tables
- Store source attribution

### v2

- Headphone/device-targeted preset packs
- Convolution import for room correction or FIR calibration

---

## Testing Plan

### Unit / API

Run:

```bash
python -m pytest tests/test_db_schema.py tests/test_server_api.py -q
```

Must cover:

- migration success
- factory preset seeding
- filter-chain generation
- safe validation bounds
- CRUD + clone behavior
- native playback request with DSP payload

### Manual listening validation

Validate on real music with these test cases:

1. Female vocal pop track
2. Male vocal jazz track
3. Dense modern pop mix
4. Large orchestral classical track
5. Electronic sub-bass track

For each preset confirm:

- no obvious clipping
- no harshness spikes
- tonal intent matches preset label
- switching to `Flat` makes the difference clear but not absurd

---

## Non-Goals For v1

- Full graph-drawing EQ editor
- FIR convolution designer inside VELVET
- Per-channel crossover matrix UI
- Automatic headphone detection
- Mobile-first deep editing parity

---

## Rollout Order

1. Fix data model and seed preset families.
2. Add ordered filters + preamp/headroom.
3. Unify native playback with browser/cast DSP behavior.
4. Ship grouped preset browser in Settings.
5. Enable clone-and-edit custom presets.
6. Add external import helpers after the core experience is stable.

---

## Source Links

- Roon MUSE overview: https://help.roonlabs.com/portal/en/kb/articles/dsp-engine
- Roon Parametric EQ: https://help.roonlabs.com/portal/en/kb/articles/dsp-engine-parametric-equalizer
- Roon Procedural EQ: https://help.roonlabs.com/portal/en/kb/articles/dsp-engine-procedural-equalizer
- Roon Headroom Management: https://help.roonlabs.com/portal/en/kb/articles/dsp-engine-headroom-management
- Roon Sample Rate Conversion: https://help.roonlabs.com/portal/kb/articles/dsp-engine-sample-rate-conversion
- Roon Audeze Presets: https://help.roonlabs.com/portal/en/kb/articles/audeze-presets
- AutoEq Wiki: https://github.com/jaakkopasanen/AutoEq/wiki/
- AutoEq Custom Parametric EQ: https://github.com/jaakkopasanen/AutoEq/wiki/Custom-Parametric-Eq
- AutoEq Repository: https://github.com/jaakkopasanen/AutoEq
- Hangout.Audio EQ Tool: https://graph.hangout.audio/

Plan complete and saved to `docs/plans/2026-04-06-dsp-eq-presets.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
