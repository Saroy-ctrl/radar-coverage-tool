# Radar Beam Elevation Angles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add min/max beam elevation angle controls that constrain radar coverage to the physical scan window of the antenna, producing a donut polygon when angles are restricted.

**Architecture:** Two new `ComputationRequest` fields (`min_beam_deg`, `max_beam_deg`) default to ±90° (transparent). The worker adds a beam gate alongside terrain visibility; `coverage_data[h]` becomes `{"outer": [...], "inner": [...] or None}`. MapView renders a Leaflet donut polygon by passing two rings to `L.polygon()`.

**Tech Stack:** PyQt6, numpy, pyproj Geod, Leaflet 1.9.4 (native multi-ring polygon support)

---

## File Map

| File | Change |
|---|---|
| `src/gui/control_panel.py` | Add `min_beam_deg`/`max_beam_deg` to `ComputationRequest`; add beam angle spinboxes + validation to `ControlPanel` |
| `src/gui/main_window.py` | Add `inner_ranges_m` dict + combined mask in worker loop; change `coverage_data` format; fix `_on_coverage_computed` area read |
| `src/gui/map_view.py` | Update `_build_coverage_features` to read new dict format and produce multi-ring `latlngs` |
| `tests/test_beam_angles.py` | New test file: unit tests for beam gate math, `ComputationRequest` defaults, and `MapView` dict format |

---

## Task 1: Git Checkpoint

Commit the current working state before touching any code. This is the revert point.

**Files:** none changed

- [ ] **Step 1: Verify working tree is clean**

```bash
git status
```

Expected: clean working tree (no unstaged changes). If there are changes, stash or commit them first.

- [ ] **Step 2: Tag current state**

```bash
git tag pre-beam-angles
```

Expected: tag created silently.

- [ ] **Step 3: Verify existing tests pass**

```bash
python -m pytest tests/test_tier1_validation.py tests/test_shadow_builder.py tests/test_dem_manager.py -v
```

Expected: all tests in those three files pass. The 3 known-failing tests are in `test_resolution_and_shadow.py` — do NOT run that file.

---

## Task 2: Write Failing Tests

Create the test file before touching implementation. All tests must fail at this point.

**Files:**
- Create: `tests/test_beam_angles.py`

- [ ] **Step 1: Create test file**

```python
# tests/test_beam_angles.py
"""
Tests for radar beam elevation angle feature.

Covers:
  - ComputationRequest default values (±90° = no constraint)
  - Beam gate numpy logic (pure math, no DEM needed)
  - MapView._build_coverage_features reading new dict format
"""

import numpy as np
import pytest
from src.gui.control_panel import ComputationRequest


# ---------------------------------------------------------------------------
# ComputationRequest defaults
# ---------------------------------------------------------------------------

def test_computation_request_has_beam_angle_defaults():
    """ComputationRequest defaults to ±90° beam angles (no constraint)."""
    req = ComputationRequest(
        radar_lat=51.5,
        radar_lon=0.0,
        site_elevation_amsl_m=0.0,
        antenna_amsl_m=10.0,
        k_factor=1.333,
        max_range_km=100.0,
        height_bands_m=[50.0],
        diffraction_guard_deg=0.5,
    )
    assert req.min_beam_deg == -90.0
    assert req.max_beam_deg == 90.0


# ---------------------------------------------------------------------------
# Beam gate math (pure numpy — no DEM, no QApplication)
# ---------------------------------------------------------------------------

def test_beam_gate_default_is_transparent():
    """With ±90° defaults beam_within is all True → combined_mask == terrain_visible."""
    target_angles = np.radians(np.array([5.0, 2.0, 1.0, 0.5, 0.1]))
    horizon_angles = np.radians(np.array([0.0, 1.0, 1.0, 1.0, 0.0]))
    terrain_visible = target_angles >= horizon_angles

    min_beam_rad = np.radians(-90.0)
    max_beam_rad = np.radians(90.0)
    beam_within = (target_angles >= min_beam_rad) & (target_angles <= max_beam_rad)
    combined_mask = terrain_visible & beam_within

    np.testing.assert_array_equal(combined_mask, terrain_visible)


def test_beam_gate_max_angle_excludes_steep_near_range():
    """max_beam=2° excludes bins whose elevation angle exceeds 2°."""
    target_angles = np.radians(np.array([10.0, 5.0, 2.0, 1.0, 0.5]))
    terrain_visible = np.ones(5, dtype=bool)  # flat terrain, all visible

    beam_within = (target_angles >= np.radians(-90.0)) & (target_angles <= np.radians(2.0))
    combined_mask = terrain_visible & beam_within

    assert not combined_mask[0]   # 10° > 2° max beam → excluded
    assert not combined_mask[1]   # 5° > 2° max beam → excluded
    assert combined_mask[2]       # exactly 2° → included
    assert combined_mask[3]       # 1° → included
    assert combined_mask[4]       # 0.5° → included


def test_beam_gate_min_angle_excludes_below_horizon():
    """min_beam=1° excludes bins whose elevation angle is below 1°."""
    target_angles = np.radians(np.array([5.0, 2.0, 1.0, 0.5, -1.0]))
    terrain_visible = np.ones(5, dtype=bool)

    beam_within = (target_angles >= np.radians(1.0)) & (target_angles <= np.radians(90.0))
    combined_mask = terrain_visible & beam_within

    assert combined_mask[0]       # 5° → included
    assert combined_mask[1]       # 2° → included
    assert combined_mask[2]       # exactly 1° → included
    assert not combined_mask[3]   # 0.5° < 1° min beam → excluded
    assert not combined_mask[4]   # -1° < 1° min beam → excluded


def test_shadow_uses_terrain_visible_not_combined_mask():
    """Shadow extraction must use terrain_visible alone, not combined_mask."""
    # When beam excludes near-range bins, those are NOT terrain-blocked.
    # Shadow = terrain blocking only.
    target_angles = np.radians(np.array([10.0, 1.0, 0.5]))
    horizon_angles = np.radians(np.array([0.0, 0.0, 2.0]))  # blocked at bin 2

    terrain_visible = target_angles >= horizon_angles
    beam_within = (target_angles >= np.radians(-90.0)) & (target_angles <= np.radians(2.0))
    combined_mask = terrain_visible & beam_within

    # bin 0: terrain_visible=True, beam_within=False (10° > 2° max)
    # bin 2: terrain_visible=False (terrain blocks), beam_within=True
    assert terrain_visible[0] is np.bool_(True)
    assert combined_mask[0] is np.bool_(False)   # beam excluded — NOT terrain blocked
    assert terrain_visible[2] is np.bool_(False)  # terrain blocked — goes to shadow
    assert combined_mask[2] is np.bool_(False)


# ---------------------------------------------------------------------------
# MapView._build_coverage_features — new dict format
# ---------------------------------------------------------------------------

def test_build_coverage_features_reads_outer_ring(qapp):
    """_build_coverage_features reads {"outer": [...], "inner": None} format."""
    from src.gui.map_view import MapView
    view = MapView()

    outer = [(51.0, 0.0), (51.1, 0.1), (51.0, 0.2), (50.9, 0.1)]
    coverage_data = {100.0: {"outer": outer, "inner": None}}

    features = view._build_coverage_features(coverage_data)

    assert len(features) == 1
    latlngs = features[0]["latlngs"]
    # Single ring: latlngs is a list-of-rings where each ring is [[lat, lon], ...]
    assert isinstance(latlngs, list)
    assert len(latlngs) == 1                         # one ring, no donut
    assert isinstance(latlngs[0][0], list)           # [[lat, lon], ...]


def test_build_coverage_features_produces_two_rings_when_inner_given(qapp):
    """_build_coverage_features appends inner ring when inner coords provided."""
    from src.gui.map_view import MapView
    view = MapView()

    outer = [(51.0, 0.0), (51.1, 0.1), (51.0, 0.2), (50.9, 0.1)]
    inner = [(51.0, 0.01), (51.05, 0.05), (51.0, 0.09), (50.95, 0.05)]
    coverage_data = {100.0: {"outer": outer, "inner": inner}}

    features = view._build_coverage_features(coverage_data)

    assert len(features) == 1
    latlngs = features[0]["latlngs"]
    assert len(latlngs) == 2    # outer ring + inner ring (donut)
```

- [ ] **Step 2: Run tests — verify all fail**

```bash
python -m pytest tests/test_beam_angles.py -v
```

Expected: all 8 tests FAIL. The first two fail with `TypeError` (missing fields on `ComputationRequest`), the beam gate math tests should actually PASS already (they only use numpy — if they pass, that's fine). MapView tests fail because `_build_coverage_features` doesn't accept dict format yet.

---

## Task 3: Extend ComputationRequest + Add UI Controls

**Files:**
- Modify: `src/gui/control_panel.py`

- [ ] **Step 1: Add fields to ComputationRequest dataclass**

In `src/gui/control_panel.py`, find the `ComputationRequest` dataclass (lines 52–66) and add two fields after `range_step_m`:

```python
@dataclass
class ComputationRequest:
    """Data class passed to compute signal."""
    radar_lat: float
    radar_lon: float
    site_elevation_amsl_m: float
    antenna_amsl_m: float
    k_factor: float
    max_range_km: float
    height_bands_m: list[float]
    diffraction_guard_deg: float
    dem_path: str = None
    obstructions_path: str = None
    azimuth_step_deg: float = 2.0
    range_step_m: float = 200.0
    min_beam_deg: float = -90.0
    max_beam_deg: float = 90.0
```

- [ ] **Step 2: Run the first two tests — they must now pass**

```bash
python -m pytest tests/test_beam_angles.py::test_computation_request_has_beam_angle_defaults -v
```

Expected: PASS.

- [ ] **Step 3: Add beam angle widgets in _create_widgets()**

In `_create_widgets()`, after the shadow mode combo block (after line ~231), add:

```python
        # === Beam Angles Group ===
        self.label_min_beam = QLabel("Min beam angle (°):")
        self.spin_min_beam = QDoubleSpinBox()
        self.spin_min_beam.setRange(-90.0, 90.0)
        self.spin_min_beam.setValue(-90.0)
        self.spin_min_beam.setDecimals(1)
        self.spin_min_beam.setSingleStep(0.5)
        self.spin_min_beam.setToolTip("Minimum radar beam elevation angle (negative = below horizon)")

        self.label_max_beam = QLabel("Max beam angle (°):")
        self.spin_max_beam = QDoubleSpinBox()
        self.spin_max_beam.setRange(-90.0, 90.0)
        self.spin_max_beam.setValue(90.0)
        self.spin_max_beam.setDecimals(1)
        self.spin_max_beam.setSingleStep(0.5)
        self.spin_max_beam.setToolTip("Maximum radar beam elevation angle")

        self.label_beam_error = QLabel("")
        self.label_beam_error.setStyleSheet("color: #ff4444; font-size: 11px;")
```

- [ ] **Step 4: Add beam angles group box in _create_layout()**

In `_create_layout()`, after the height bands group block (after `layout.addWidget(group_heights)`), add:

```python
        # === Beam Angles Group ===
        group_beam = QGroupBox("Beam Elevation Angles")
        gb_layout = QVBoxLayout()
        beam_min_row = QHBoxLayout()
        beam_min_row.addWidget(self.label_min_beam)
        beam_min_row.addWidget(self.spin_min_beam)
        gb_layout.addLayout(beam_min_row)
        beam_max_row = QHBoxLayout()
        beam_max_row.addWidget(self.label_max_beam)
        beam_max_row.addWidget(self.spin_max_beam)
        gb_layout.addLayout(beam_max_row)
        gb_layout.addWidget(self.label_beam_error)
        group_beam.setLayout(gb_layout)
        layout.addWidget(group_beam)
```

- [ ] **Step 5: Add validation and read values in _on_compute_clicked()**

In `_on_compute_clicked()`, after `if self.is_computing: return` and before the `site_elev = ...` line, add:

```python
        min_beam = self.spin_min_beam.value()
        max_beam = self.spin_max_beam.value()
        if min_beam >= max_beam:
            self.label_beam_error.setText("Min must be less than max.")
            return
        self.label_beam_error.setText("")
```

Then extend the `ComputationRequest(...)` constructor call (in the same method) to include the two new fields after `range_step_m=rng_step`:

```python
            min_beam_deg=min_beam,
            max_beam_deg=max_beam,
```

- [ ] **Step 6: Run the defaults test again to confirm no regression**

```bash
python -m pytest tests/test_beam_angles.py::test_computation_request_has_beam_angle_defaults -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/gui/control_panel.py tests/test_beam_angles.py
git commit -m "feat: add beam elevation angle fields to ComputationRequest and ControlPanel UI"
```

---

## Task 4: Update ComputationWorker — Combined Mask + Inner Radius

**Files:**
- Modify: `src/gui/main_window.py`

- [ ] **Step 1: Add inner_ranges_m dict and precompute beam radians before the azimuth loop**

In `ComputationWorker.run()`, find the lines (after `coverage_ranges_m = {h: ...}` and before `_min_h_agl = ...`):

```python
            coverage_ranges_m = {h: np.zeros(n_az, dtype=np.float64) for h in heights_agl}

            _min_h_agl = min(heights_agl) if heights_agl else None
```

Replace with:

```python
            coverage_ranges_m = {h: np.zeros(n_az, dtype=np.float64) for h in heights_agl}
            inner_ranges_m    = {h: np.zeros(n_az, dtype=np.float64) for h in heights_agl}

            min_beam_rad = np.radians(req.min_beam_deg)
            max_beam_rad = np.radians(req.max_beam_deg)

            _min_h_agl = min(heights_agl) if heights_agl else None
```

- [ ] **Step 2: Replace visibility check with two-gate combined mask inside the per-height loop**

Find the per-height loop body (inside `for h_agl in heights_agl:`). The current code is:

```python
                    # Visible where target angle >= cumulative horizon (skip bin 0 = antenna)
                    visible_mask = target_angles[1:] >= horizon_angles[1:]
                    visible_idx  = np.where(visible_mask)[0]

                    # Use LAST visible bin as coverage range (outer boundary).
                    if len(visible_idx) > 0:
                        max_r = float(ranges[1:][visible_idx[-1]])
                    else:
                        max_r = float(ranges[1])   # nothing visible: one step

                    coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)

                    if h_agl == _min_h_agl:
                        segs = extract_blocked_segments(visible_mask, ranges, az)
                        all_shadow_segments.extend(segs)
```

Replace with:

```python
                    # Gate 1: terrain visibility (cumulative horizon)
                    terrain_visible = target_angles[1:] >= horizon_angles[1:]
                    # Gate 2: beam elevation window
                    beam_within     = (target_angles[1:] >= min_beam_rad) & \
                                      (target_angles[1:] <= max_beam_rad)
                    combined_mask   = terrain_visible & beam_within

                    visible_idx = np.where(combined_mask)[0]

                    if len(visible_idx) > 0:
                        max_r = float(ranges[1:][visible_idx[-1]])  # last visible = outer boundary
                        min_r = float(ranges[1:][visible_idx[0]])   # first visible = inner boundary
                    else:
                        max_r = float(ranges[1])
                        min_r = float(ranges[1])

                    coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)
                    inner_ranges_m[h_agl][i]    = min_r

                    # Shadow uses terrain_visible only — beam exclusion ≠ terrain blocking
                    if h_agl == _min_h_agl:
                        segs = extract_blocked_segments(terrain_visible, ranges, az)
                        all_shadow_segments.extend(segs)
```

- [ ] **Step 3: Change coverage_data polygon build to new dict format**

Find the polygon-build block after the azimuth loop. The current code is:

```python
            coverage_data = {}
            for h in heights_agl:
                ranges_h = coverage_ranges_m[h]
                # Replace any zero-range azimuths with a tiny offset so the polygon isn't degenerate
                ranges_h = np.where(ranges_h < RANGE_STEP_M, RANGE_STEP_M, ranges_h)

                lons_p, lats_p, _ = GEOD.fwd(
                    np.full(n_az, ant_lon),
                    np.full(n_az, ant_lat),
                    azimuths,
                    ranges_h
                )
                # Wrap around: first point appended to close the ring
                lat_list = lats_p.tolist() + [lats_p[0]]
                lon_list = lons_p.tolist() + [lons_p[0]]
                coverage_data[h] = list(zip(lat_list, lon_list))
```

Replace with:

```python
            coverage_data = {}
            for h in heights_agl:
                # ── Outer polygon ────────────────────────────────────────────
                ranges_h = coverage_ranges_m[h].copy()
                ranges_h = np.where(ranges_h < RANGE_STEP_M, RANGE_STEP_M, ranges_h)
                lons_out, lats_out, _ = GEOD.fwd(
                    np.full(n_az, ant_lon),
                    np.full(n_az, ant_lat),
                    azimuths,
                    ranges_h
                )
                outer_list = list(zip(
                    lats_out.tolist() + [lats_out[0]],
                    lons_out.tolist() + [lons_out[0]]
                ))

                # ── Inner polygon (donut hole from max_beam cutoff) ──────────
                inner_r_arr = inner_ranges_m[h].copy()
                inner = None
                if np.any(inner_r_arr > RANGE_STEP_M):
                    inner_r_arr = np.where(inner_r_arr < RANGE_STEP_M, RANGE_STEP_M, inner_r_arr)
                    lons_in, lats_in, _ = GEOD.fwd(
                        np.full(n_az, ant_lon),
                        np.full(n_az, ant_lat),
                        azimuths,
                        inner_r_arr
                    )
                    inner = list(zip(
                        lats_in.tolist() + [lats_in[0]],
                        lons_in.tolist() + [lons_in[0]]
                    ))

                coverage_data[h] = {"outer": outer_list, "inner": inner}
```

- [ ] **Step 4: Run existing Tier 1 tests — must still pass**

```bash
python -m pytest tests/test_tier1_validation.py -v
```

Expected: all 5 tests pass. (The worker is not exercised by Tier 1 tests, but this confirms no import-time breakage.)

- [ ] **Step 5: Commit**

```bash
git add src/gui/main_window.py
git commit -m "feat: add beam gate combined mask and inner_ranges_m to ComputationWorker"
```

---

## Task 5: Fix _on_coverage_computed Area Read

**Files:**
- Modify: `src/gui/main_window.py`

The area computation in `_on_coverage_computed` reads `coverage_data[largest_h]` as a list. With the new dict format this raises `TypeError`.

- [ ] **Step 1: Fix the one broken read**

Find `_on_coverage_computed` (around line 599). The current code is:

```python
                largest_h = max(coverage_data.keys())
                coords = coverage_data[largest_h]
                if len(coords) >= 3:
                    # shapely Polygon takes (lon, lat); coords are (lat, lon)
                    poly = Polygon([(c[1], c[0]) for c in coords])
```

Replace with:

```python
                largest_h = max(coverage_data.keys())
                coords = coverage_data[largest_h]["outer"]
                if len(coords) >= 3:
                    # shapely Polygon takes (lon, lat); coords are (lat, lon)
                    poly = Polygon([(c[1], c[0]) for c in coords])
```

- [ ] **Step 2: Run beam gate math tests to confirm no regression**

```bash
python -m pytest tests/test_beam_angles.py -k "not qapp" -v
```

Expected: all non-GUI beam gate tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/gui/main_window.py
git commit -m "fix: read coverage_data[h]['outer'] in _on_coverage_computed after dict format change"
```

---

## Task 6: Update MapView._build_coverage_features

**Files:**
- Modify: `src/gui/map_view.py`

- [ ] **Step 1: Run the MapView tests — verify they still fail**

```bash
python -m pytest tests/test_beam_angles.py::test_build_coverage_features_reads_outer_ring tests/test_beam_angles.py::test_build_coverage_features_produces_two_rings_when_inner_given -v
```

Expected: FAIL — `_build_coverage_features` still tries `list(coords)` on a dict.

- [ ] **Step 2: Replace _build_coverage_features**

In `src/gui/map_view.py`, replace the entire `_build_coverage_features` method (lines 132–166) with:

```python
    def _build_coverage_features(self, coverage_data: dict) -> list:
        """
        Convert coverage_data dict into feature dicts for _generate_leaflet_html.

        Accepts new dict format: {height_m: {"outer": [(lat,lon),...], "inner": [...] or None}}
        Also accepts legacy list format: {height_m: [(lat,lon),...]} for backwards compatibility.

        Each feature: {height_m, area_km2, latlngs: [outer_ring] or [outer_ring, inner_ring]}
        where each ring is [[lat, lon], ...]. Leaflet L.polygon() natively renders a donut
        when two rings are passed.
        """
        features = []
        for height_m, band_data in coverage_data.items():
            # Normalise to dict format
            if isinstance(band_data, dict):
                outer_coords = list(band_data.get("outer", []))
                inner_coords = band_data.get("inner")
            else:
                outer_coords = list(band_data)
                inner_coords = None

            if not outer_coords:
                continue

            # Drop closing duplicate if present (Leaflet auto-closes polygons)
            if len(outer_coords) > 1 and outer_coords[0] == outer_coords[-1]:
                outer_coords = outer_coords[:-1]

            if len(outer_coords) < 3:
                continue

            area_km2 = self._compute_polygon_area_km2(outer_coords)
            outer_ring = [[c[0], c[1]] for c in outer_coords]

            latlngs = [outer_ring]

            if inner_coords and len(inner_coords) >= 3:
                inner_list = list(inner_coords)
                if len(inner_list) > 1 and inner_list[0] == inner_list[-1]:
                    inner_list = inner_list[:-1]
                if len(inner_list) >= 3:
                    latlngs.append([[c[0], c[1]] for c in inner_list])

            features.append({
                "height_m": float(height_m),
                "area_km2": area_km2,
                "latlngs": latlngs,
            })

        # Highest height first: drawn below lower bands so lower bands paint on top
        features.sort(key=lambda f: f["height_m"], reverse=True)
        return features
```

- [ ] **Step 3: Run the MapView tests — must now pass**

```bash
python -m pytest tests/test_beam_angles.py::test_build_coverage_features_reads_outer_ring tests/test_beam_angles.py::test_build_coverage_features_produces_two_rings_when_inner_given -v
```

Expected: both PASS.

- [ ] **Step 4: Run all beam angle tests**

```bash
python -m pytest tests/test_beam_angles.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Run full test suite (excluding known-failing file)**

```bash
python -m pytest tests/test_tier1_validation.py tests/test_shadow_builder.py tests/test_dem_manager.py tests/test_beam_angles.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/gui/map_view.py
git commit -m "feat: update _build_coverage_features for donut polygon — reads new dict format, multi-ring latlngs"
```

---

## Task 7: Smoke Test

Verify the full app behaves correctly end-to-end with a real DEM.

**Files:** none changed

- [ ] **Step 1: Launch the app**

```bash
python main.py
```

- [ ] **Step 2: Test A — Default beam angles (zero visual change)**

1. Load a DEM, set any site, click COMPUTE with default beam angles (−90°/+90°).
2. Coverage polygons must look identical to before this change.
3. Shadow zones must be unchanged.
4. Status bar area readout must appear (not crash).

- [ ] **Step 3: Test B — Restricted beam angles produce donut**

1. Set min beam = **0°**, max beam = **10°**.
2. Click COMPUTE.
3. Expected: coverage polygons show a donut hole near the antenna (inner ring cuts very-close targets whose elevation angle exceeds 10°). The donut hole is typically small (hundreds of metres) but visible when zoomed in.
4. Shadow zones must remain unchanged (beam exclusion ≠ terrain blocking).

- [ ] **Step 4: Test C — Validation blocks bad input**

1. Set min beam = **20°**, max beam = **5°** (min ≥ max).
2. Click COMPUTE.
3. Expected: red warning label "Min must be less than max." appears; computation does NOT start.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: radar beam elevation angles complete — donut polygon, UI controls, beam gate in worker"
```

---

## Revert Instructions

If anything goes wrong at any task, revert to the tagged checkpoint:

```bash
git checkout pre-beam-angles
```

Or to reset the branch to that point:

```bash
git reset --hard pre-beam-angles
```
