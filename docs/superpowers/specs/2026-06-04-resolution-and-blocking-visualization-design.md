# Design Spec: Resolution Presets & Terrain-Blocking Visualization

**Date:** 2026-06-04
**Project:** Radar Coverage Analysis Tool
**Goal:** Match Cambridge Pixel SPx visual fidelity — finer azimuth/range resolution + terrain-blocking spike overlay

---

## Context

The current tool uses hardcoded `AZIMUTH_STEP = 2°` and `RANGE_STEP_M = 200m`, producing 180 azimuths × ~1,000 range bins per computation. This makes coverage polygon edges blocky and misses the detailed terrain-blocking spike pattern prominent in the Cambridge Pixel SPx reference tool.

This spec addresses Phase A only: resolution improvements and the shadow/blocking visualization. UI feature parity (start range, beam angles, transparency slider, DMS coords, azimuth extent) is out of scope.

---

## Requirements

1. User can select computation resolution from four named presets.
2. UI shows an estimated compute time next to the preset selector.
3. Terrain-blocked zones (shadow from visible boundary to max range) are rendered as dark-red radial wedge polygons on the Leaflet map.
4. Shadow layer uses only the **lowest enabled height band** — the smallest AGL height value among bands whose checkbox is checked in the control panel (most restrictive, largest blocked area).
5. Coverage polygons are painted over the shadow layer (shadow rendered first in HTML).
6. No changes to backend physics engines (`EarthModel`, `VisibilityEngine`, `ObstructionEngine`).

---

## Resolution Presets

| Preset | Azimuth Step | Range Step | Azimuths | Range Bins (200km) | Est. Time |
|--------|-------------|------------|----------|--------------------|-----------|
| Fast   | 2°          | 200m       | 180      | ~1,000             | ~30s      |
| Standard | 1°        | 100m       | 360      | ~2,000             | ~2 min    |
| High   | 0.5°        | 100m       | 720      | ~2,000             | ~4 min    |
| Ultra  | 0.5°        | 50m        | 720      | ~4,000             | ~8 min    |

Time estimates are hardcoded initially. After each run, actual elapsed time is stored in-session and used to recalibrate displayed estimates (anchored to Fast preset).

---

## Architecture — What Changes

### 1. `src/gui/control_panel.py`

**`ComputationRequest` dataclass** — add two fields:
```python
azimuth_step_deg: float = 2.0
range_step_m: float = 200.0
```

**`ControlPanel` widget** — add "Computation Resolution" group below the Diffraction Guard row:
- `QComboBox` with items: `["Fast (~30s)", "Standard (~2min)", "High (~4min)", "Ultra (~8min)"]`
- `QLabel` next to it showing `"Est. ~Xs"` — updates on combo change and recalibrates after each run
- On combo change, set `azimuth_step_deg` and `range_step_m` accordingly before emitting `compute_requested`

Preset → parameter mapping:
```python
RESOLUTION_PRESETS = {
    "Fast":     (2.0,  200.0),
    "Standard": (1.0,  100.0),
    "High":     (0.5,  100.0),
    "Ultra":    (0.5,   50.0),
}
```

### 2. `src/gui/main_window.py` — `ComputationWorker.run()`

Replace hardcoded constants:
```python
# Before
RANGE_STEP_M = 200.0
AZIMUTH_STEP = 2

# After
RANGE_STEP_M = req.range_step_m
AZIMUTH_STEP = req.azimuth_step_deg
```

No other changes to worker logic. `azimuths = np.arange(0, 360, AZIMUTH_STEP)` and `ranges = np.arange(0.0, max_range_m + RANGE_STEP_M, RANGE_STEP_M)` already use these variables correctly.

**Timing:** Record wall-clock time around the azimuth loop. Emit elapsed seconds via a new signal `computation_timed = pyqtSignal(float)` so `MainWindow` can recalibrate the estimate label.

### 3. `src/gui/map_view.py`

**`_build_shadow_features(coverage_data, max_range_m, azimuth_step_deg)`** — new method:
- Finds the lowest height band key from `coverage_data` (minimum key value among all present height bands)
- For each azimuth `i` at bearing `az = i * azimuth_step_deg`:
  - `inner_r = coverage_ranges_m[lowest_h][i]`
  - `outer_r = max_range_m`
  - If `inner_r >= outer_r`: skip (no shadow — full coverage at this azimuth)
  - Build a thin wedge polygon using 4 geodetic points:
    - `(az - half_step)` projected to `inner_r` and `outer_r`
    - `(az + half_step)` projected to `outer_r` and `inner_r`
  - Use `GEOD.fwd()` for all 4 points
- Returns list of `{"latlngs": [[lat,lon],...]}` dicts

**`_generate_leaflet_html()`** — updated signature:
```python
def _generate_leaflet_html(self, lat, lon, coverage_features, shadow_features=None)
```
- Shadow polygons rendered **first** in the JS block (beneath coverage):
  ```js
  L.polygon(latlngs, {color:'#8b0000', fillColor:'#8b0000', weight:0,
                       opacity:0, fillOpacity:0.55}).addTo(map);
  ```
- Coverage polygons rendered after (painted on top)

**`update_coverage()`** — pass `max_range_m` and `azimuth_step_deg` so `_build_shadow_features` can be called. These are stored on the widget after computation completes.

`coverage_data` dict passed via signal already has `{height_m: [(lat,lon),...]}` — shadow builder needs the raw `coverage_ranges_m` arrays, not the pre-projected lat/lon ring. Two options:

- **Option A (chosen):** Emit a separate `shadow_data` signal from the worker carrying `{lowest_h: [ranges_m per az]}` directly — avoids re-projecting coordinates in MapView.
- Option B: Re-derive from polygon coords (lossy, complex). Rejected.

So `ComputationWorker` emits an additional signal:
```python
shadow_data_ready = pyqtSignal(dict)
# payload: {
#   "ranges_m": list[float],      ← per-azimuth max visible range for lowest height band
#   "azimuth_step_deg": float,    ← step used in this run
#   "max_range_m": float,         ← instrumented range limit
# }
```
`MainWindow` connects this to `MapView.set_shadow_data(ant_lat, ant_lon, payload)`.

### 4. `src/gui/polar_view.py`

No changes. The existing horizon fill (`ax.fill_between` in dark red) already shows terrain obstruction on the polar diagram.

---

## Data Flow

```
ControlPanel
  └─ compute_requested(ComputationRequest{azimuth_step_deg, range_step_m})
       └─ ComputationWorker.run()
            ├─ coverage_computed → MainWindow → MapView.update_coverage()
            ├─ shadow_data_ready → MainWindow → MapView.set_shadow_data()
            ├─ polar_data_ready  → MainWindow → PolarView.update_data()
            └─ computation_timed → MainWindow → ControlPanel.recalibrate_estimate()
```

---

## Shadow Wedge Geometry

Each shadow wedge is a 4-point closed polygon:

```
P1 = GEOD.fwd(ant, az - half_step, inner_r)   ← inner-left
P2 = GEOD.fwd(ant, az - half_step, outer_r)   ← outer-left
P3 = GEOD.fwd(ant, az + half_step, outer_r)   ← outer-right
P4 = GEOD.fwd(ant, az + half_step, inner_r)   ← inner-right
```

`half_step = azimuth_step_deg / 2`

For Ultra preset (0.5° step), each wedge is 0.5° wide — producing thin, sharp spikes that closely match Cambridge Pixel's visual. For Fast preset (2°), wedges are wider and blockier (acceptable at that resolution).

---

## Error Handling

- If `coverage_data` is empty when building shadow features: skip silently, no shadow layer rendered.
- If `inner_r <= 0` (degenerate): use `RANGE_STEP_M` as minimum inner radius.
- GEOD projection errors in shadow builder: skip that azimuth, log warning.

---

## Out of Scope (Phase B)

- Start range / inner donut hole
- Beam elevation angle controls
- Coverage transparency slider
- Map brightness/contrast sliders
- DMS coordinate input
- Azimuth extent (partial sector)
- Above Sea Level vs Ground target height toggle
- True offline (local Leaflet + OSM tiles)
- GeoJSON export fix
- DEM preprocessor wiring

---

## Files Changed

| File | Type of change |
|------|---------------|
| `src/gui/control_panel.py` | Add resolution group widget; extend `ComputationRequest` |
| `src/gui/main_window.py` | Remove hardcoded constants; connect new signals |
| `src/gui/map_view.py` | Add shadow feature builder + rendering |
| `src/gui/polar_view.py` | None |
