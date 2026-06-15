# Shadow Continuous Polygon + DEM Reliability Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a smooth shapely-merged shadow polygon rendering mode alongside the existing per-azimuth wedge mode, and fix two DEM reliability gaps (void-fill pipeline not wired, single-tile limit).

**Architecture:** Shadow segments (all blocked range intervals per azimuth) are extracted during computation and emitted alongside the existing `ranges_m` list. A new `shadow_builder.py` module converts those segments to shapely wedges and merges them via `unary_union`, producing smooth terrain-contour blobs. MapView gains a `shadow_mode` toggle between "Wedge" (current) and "Polygon" (new). DEM loading is upgraded in two separate tasks: void-fill is wired inline into the worker, and a new `DEMManager` auto-discovers and mosaics neighboring SRTM tiles.

**Tech Stack:** NumPy (run-length segment extraction), Shapely + pyproj (wedge build + merge), rasterio.merge (multi-tile mosaic), PyQt6 QComboBox (mode toggle), existing `DEMPreprocessor.fill_voids()`.

---

## File Map

| Action | Path | What changes |
|---|---|---|
| **Create** | `src/shadow_builder.py` | `extract_blocked_segments()`, `build_merged_shadow_geojson()` |
| **Create** | `src/dem_manager.py` | `find_required_tiles()`, `open_mosaic()` |
| **Modify** | `src/gui/main_window.py` | Worker: collect segments, emit void%, wire DEMManager + fill_voids |
| **Modify** | `src/gui/map_view.py` | `shadow_mode`, continuous render path, GeoJSON JS injection |
| **Modify** | `src/gui/control_panel.py` | Shadow mode QComboBox + `shadow_mode_changed` signal |
| **Create** | `tests/test_shadow_builder.py` | Segment extraction + merge tests |
| **Create** | `tests/test_dem_manager.py` | Tile discovery tests |

---

## Task 1: Create `src/shadow_builder.py` — segment extraction function

**Files:**
- Create: `src/shadow_builder.py`

- [ ] **Step 1: Write the file with `extract_blocked_segments()`**

```python
# src/shadow_builder.py
"""
Shadow geometry builder.

Two public functions:
  extract_blocked_segments — run-length encode blocked range bins for one azimuth.
  build_merged_shadow_geojson — merge all segments across all azimuths into smooth
                                 shapely-union blob polygons, returned as GeoJSON.
"""

import numpy as np
from pyproj import Geod

GEOD = Geod(ellps='WGS84')
MIN_INNER_R_M = 200.0   # prevent degenerate point polygon at antenna


def extract_blocked_segments(visible_mask, ranges, az_deg):
    """
    Return all contiguous blocked intervals for one azimuth radial.

    Args:
        visible_mask: bool ndarray shape (N,) — True = visible.
                      Index j corresponds to ranges[j+1].
        ranges:       float ndarray shape (N+1,) — ranges[0]=0, ranges[1..N]=range bins.
        az_deg:       azimuth of this radial (degrees, 0=North)

    Returns:
        list of (az_deg: float, inner_r_m: float, outer_r_m: float)
        Empty list if no blocked bins exist.
    """
    blocked = (~visible_mask).astype(np.int8)
    if blocked.sum() == 0:
        return []

    # Pad with sentinel zeros so np.diff detects runs touching the boundary
    padded = np.concatenate([[0], blocked, [0]])
    diffs  = np.diff(padded)                          # length len(blocked)+1

    starts = np.where(diffs == 1)[0]   # index in blocked where run begins
    ends   = np.where(diffs == -1)[0]  # index in blocked just past run end

    # blocked[s]   → ranges[s+1]   (first blocked bin)
    # blocked[e-1] → ranges[e]     (last blocked bin)
    result = []
    for s, e in zip(starts, ends):
        inner_r = max(float(ranges[s + 1]), MIN_INNER_R_M)
        outer_r = float(ranges[e])
        if outer_r > inner_r:
            result.append((float(az_deg), inner_r, outer_r))
    return result
```

- [ ] **Step 2: Verify file exists**

```
python -c "from src.shadow_builder import extract_blocked_segments; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```
git add src/shadow_builder.py
git commit -m "feat: add shadow_builder module with extract_blocked_segments"
```

---

## Task 2: Tests for `extract_blocked_segments`

**Files:**
- Create: `tests/test_shadow_builder.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_shadow_builder.py
import numpy as np
import pytest
from src.shadow_builder import extract_blocked_segments


def _make_flat_terrain_mask(n_bins, all_visible=True):
    """Return a visible_mask of length n_bins, all True or all False."""
    return np.full(n_bins, all_visible, dtype=bool)


def test_flat_terrain_no_segments():
    """All visible → no shadow segments."""
    ranges = np.arange(0.0, 50100.0, 100.0)       # 0..50000, step 100m
    visible = np.ones(len(ranges) - 1, dtype=bool)
    segs = extract_blocked_segments(visible, ranges, az_deg=0.0)
    assert segs == []


def test_all_blocked_one_segment():
    """All blocked → one segment spanning full range."""
    ranges = np.arange(0.0, 10100.0, 100.0)        # 0..10000, step 100m
    visible = np.zeros(len(ranges) - 1, dtype=bool)
    segs = extract_blocked_segments(visible, ranges, az_deg=90.0)
    assert len(segs) == 1
    az, inner_r, outer_r = segs[0]
    assert az == 90.0
    assert inner_r == 200.0       # clamped to MIN_INNER_R_M
    assert outer_r == ranges[-1]  # 10000 m


def test_single_ridge_two_segments():
    """
    Ridge blocks bins 10–20 (inner shadow) and the outer region is visible,
    then a second obstacle blocks bins 40–50.
    visible_mask: T*10, F*11, T*19, F*11, T*rest
    """
    step = 100.0
    n = 100
    ranges = np.arange(0.0, (n + 1) * step, step)    # 0..10000, 101 points
    visible = np.ones(n, dtype=bool)
    visible[10:21] = False   # blocked from bin 10 to bin 20 inclusive
    visible[40:51] = False   # second blocked run

    segs = extract_blocked_segments(visible, ranges, az_deg=45.0)
    assert len(segs) == 2

    az0, in0, out0 = segs[0]
    assert az0 == 45.0
    # First run: j=10..20 → ranges[11]=1100, ranges[21]=2100
    assert in0 == pytest.approx(1100.0)
    assert out0 == pytest.approx(2100.0)

    az1, in1, out1 = segs[1]
    # Second run: j=40..50 → ranges[41]=4100, ranges[51]=5100
    assert in1 == pytest.approx(4100.0)
    assert out1 == pytest.approx(5100.0)


def test_inner_r_clamped_to_minimum():
    """Blocked bin at index 0 (range = step) → inner_r clamped to 200 m."""
    step = 50.0
    ranges = np.arange(0.0, 5050.0, step)         # range[1] = 50 m < MIN_INNER_R_M
    visible = np.ones(len(ranges) - 1, dtype=bool)
    visible[0:5] = False                           # blocked from range 50 m (< 200 m)

    segs = extract_blocked_segments(visible, ranges, az_deg=180.0)
    assert len(segs) == 1
    _, inner_r, _ = segs[0]
    assert inner_r == 200.0   # clamped, not 50 m
```

- [ ] **Step 2: Run tests**

```
python -m pytest tests/test_shadow_builder.py -v
```
Expected: 4 PASSED

- [ ] **Step 3: Commit**

```
git add tests/test_shadow_builder.py
git commit -m "test: shadow segment extraction — flat, all-blocked, two-run, clamp cases"
```

---

## Task 3: Add `build_merged_shadow_geojson()` to `shadow_builder.py`

**Files:**
- Modify: `src/shadow_builder.py`

- [ ] **Step 1: Append the merge function to `shadow_builder.py`**

Open `src/shadow_builder.py` and append after the existing `extract_blocked_segments` function:

```python

def _wedge_polygon(ant_lat, ant_lon, az_deg, inner_r, outer_r, half_az_deg):
    """
    Build a shapely Polygon for one (azimuth, inner_r, outer_r) shadow segment.
    Uses 4 geodetic corners so adjacent azimuths share edges and union cleanly.
    """
    from shapely.geometry import Polygon
    left_az  = (az_deg - half_az_deg) % 360
    right_az = (az_deg + half_az_deg) % 360

    lon_il, lat_il, _ = GEOD.fwd(ant_lon, ant_lat, left_az,  inner_r)
    lon_ir, lat_ir, _ = GEOD.fwd(ant_lon, ant_lat, right_az, inner_r)
    lon_ol, lat_ol, _ = GEOD.fwd(ant_lon, ant_lat, left_az,  outer_r)
    lon_or, lat_or, _ = GEOD.fwd(ant_lon, ant_lat, right_az, outer_r)

    return Polygon([
        (lon_il, lat_il),
        (lon_ol, lat_ol),
        (lon_or, lat_or),
        (lon_ir, lat_ir),
        (lon_il, lat_il),
    ])


def build_merged_shadow_geojson(ant_lat, ant_lon, shadow_segments, azimuth_step_deg):
    """
    Convert shadow segments to a GeoJSON FeatureCollection with smooth merged polygons.

    Adjacent azimuth wedges covering the same terrain feature are united by
    shapely.unary_union into organic blob shapes, matching Cambridge Pixel's
    appearance.

    Args:
        ant_lat, ant_lon:    antenna WGS84 position
        shadow_segments:     list of (az_deg, inner_r_m, outer_r_m)
        azimuth_step_deg:    azimuth resolution used in the computation run

    Returns:
        dict: GeoJSON FeatureCollection (Polygon or MultiPolygon geometry)
    """
    from shapely.ops import unary_union
    from shapely.geometry import mapping

    if not shadow_segments:
        return {"type": "FeatureCollection", "features": []}

    half = azimuth_step_deg / 2.0
    polys = [
        _wedge_polygon(ant_lat, ant_lon, az, inner_r, outer_r, half)
        for az, inner_r, outer_r in shadow_segments
    ]
    valid_polys = [p for p in polys if p.is_valid and not p.is_empty]
    if not valid_polys:
        return {"type": "FeatureCollection", "features": []}

    merged = unary_union(valid_polys)
    if merged.is_empty:
        return {"type": "FeatureCollection", "features": []}

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(merged),
            "properties": {}
        }]
    }
```

- [ ] **Step 2: Verify import**

```
python -c "from src.shadow_builder import build_merged_shadow_geojson; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```
git add src/shadow_builder.py
git commit -m "feat: shadow_builder — add shapely wedge build and unary_union merge"
```

---

## Task 4: Tests for `build_merged_shadow_geojson`

**Files:**
- Modify: `tests/test_shadow_builder.py`

- [ ] **Step 1: Append merge tests**

Open `tests/test_shadow_builder.py` and append:

```python
from src.shadow_builder import build_merged_shadow_geojson


# Tenerife-like antenna for geodetic tests
ANT_LAT = 28.2724
ANT_LON = -16.6425


def test_empty_segments_returns_empty_fc():
    fc = build_merged_shadow_geojson(ANT_LAT, ANT_LON, [], azimuth_step_deg=2.0)
    assert fc["type"] == "FeatureCollection"
    assert fc["features"] == []


def test_single_segment_produces_one_feature():
    segs = [(90.0, 5_000.0, 20_000.0)]
    fc = build_merged_shadow_geojson(ANT_LAT, ANT_LON, segs, azimuth_step_deg=2.0)
    assert len(fc["features"]) == 1
    geom = fc["features"][0]["geometry"]
    assert geom["type"] in ("Polygon", "MultiPolygon")


def test_adjacent_same_range_merges_to_fewer_features():
    """
    10 consecutive azimuth bins all blocked in the same range band
    should merge into a single Polygon (not 10 separate features).
    """
    segs = [(float(az), 10_000.0, 30_000.0) for az in range(0, 20, 2)]  # 10 bins
    fc = build_merged_shadow_geojson(ANT_LAT, ANT_LON, segs, azimuth_step_deg=2.0)
    # After union we expect exactly 1 Polygon or 1 MultiPolygon with 1 geom
    assert len(fc["features"]) == 1
    geom_type = fc["features"][0]["geometry"]["type"]
    assert geom_type == "Polygon"   # tight adjacent wedges → single merged polygon


def test_two_separated_bands_remain_separate():
    """
    Segments at az=0° and az=180° (opposite sides) must NOT merge."""
    segs = [(0.0, 5_000.0, 15_000.0), (180.0, 5_000.0, 15_000.0)]
    fc = build_merged_shadow_geojson(ANT_LAT, ANT_LON, segs, azimuth_step_deg=2.0)
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "MultiPolygon"
```

- [ ] **Step 2: Run all shadow_builder tests**

```
python -m pytest tests/test_shadow_builder.py -v
```
Expected: 8 PASSED

- [ ] **Step 3: Commit**

```
git add tests/test_shadow_builder.py
git commit -m "test: shadow merge — empty, single segment, adjacent merge, separated stay split"
```

---

## Task 5: Extend `ComputationWorker` to collect all blocked segments

**Files:**
- Modify: `src/gui/main_window.py:55–254`

- [ ] **Step 1: Add segment collection inside the azimuth loop**

In `ComputationWorker.run()`, make three targeted edits:

**Edit A** — Before the azimuth loop (after line 157 `coverage_ranges_m = ...`), add:

```python
            from src.shadow_builder import extract_blocked_segments
            _min_h_agl = min(heights_agl) if heights_agl else None
            all_shadow_segments = []   # list of (az_deg, inner_r_m, outer_r_m)
```

**Edit B** — Inside the `for h_agl in heights_agl` loop, AFTER line 235 `coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)`, add:

```python
                    if h_agl == _min_h_agl:
                        segs = extract_blocked_segments(visible_mask, ranges, az)
                        all_shadow_segments.extend(segs)
```

**Edit C** — Replace lines 247–254 (the `shadow_payload` block) with:

```python
            # Shadow data for MapView: both modes use the same payload.
            # "ranges_m" drives the fast wedge mode.
            # "shadow_segments" drives the smooth polygon mode.
            if heights_agl:
                _lowest_h = min(heights_agl)
                shadow_payload = {
                    "ranges_m":        coverage_ranges_m[_lowest_h].tolist(),
                    "shadow_segments": all_shadow_segments,
                    "azimuth_step_deg": float(AZIMUTH_STEP),
                    "max_range_m":      float(max_range_m),
                }
                self.shadow_data_ready.emit(shadow_payload)
```

- [ ] **Step 2: Run the Tier-1 tests to confirm no regression**

```
python -m pytest tests/test_tier1_validation.py -v
```
Expected: V1–V5 all PASS

- [ ] **Step 3: Commit**

```
git add src/gui/main_window.py
git commit -m "feat: worker emits all blocked shadow segments alongside ranges_m"
```

---

## Task 6: Extend `MapView` with continuous polygon rendering mode

**Files:**
- Modify: `src/gui/map_view.py`

- [ ] **Step 1: Add `_shadow_mode` attribute and `set_shadow_mode()` method**

In `MapView.__init__()`, after line 50 `self._max_range_m = 0.0`, add:

```python
        self._shadow_mode = "Wedge"           # "Wedge" | "Polygon"
        self._shadow_geojson = None           # stored GeoJSON dict for polygon mode
        self._shadow_segments_cache = []      # stored segments for polygon mode
        self._shadow_az_step_cache = 2.0
```

After the `cleanup()` method at the end of the class, add:

```python
    def set_shadow_mode(self, mode: str):
        """
        Switch shadow rendering mode. Triggers a re-render with current data.
        mode: "Wedge" (per-azimuth wedge polygons) or "Polygon" (shapely-merged blobs)
        """
        self._shadow_mode = mode
        if mode == "Polygon" and self._shadow_segments_cache:
            from src.shadow_builder import build_merged_shadow_geojson
            self._shadow_geojson = build_merged_shadow_geojson(
                self.radar_lat, self.radar_lon,
                self._shadow_segments_cache,
                self._shadow_az_step_cache,
            )
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)
```

- [ ] **Step 2: Update `set_shadow_data()` to cache segments and build GeoJSON when in Polygon mode**

Replace the existing `set_shadow_data()` method (lines 441–459) with:

```python
    def set_shadow_data(self, ant_lat: float, ant_lon: float, payload: dict):
        """
        Rebuild shadow geometry from computation payload and re-render.

        payload keys:
            "ranges_m"        — per-azimuth last-visible range (wedge mode)
            "shadow_segments" — list of (az_deg, inner_r, outer_r) tuples (polygon mode)
            "azimuth_step_deg"
            "max_range_m"
        """
        self._max_range_m = payload["max_range_m"]
        self._shadow_az_step_cache = payload["azimuth_step_deg"]
        self._shadow_segments_cache = payload.get("shadow_segments", [])

        # Always build the wedge features (used in Wedge mode and as fallback)
        self._shadow_features = self._build_shadow_features(
            ant_lat=ant_lat,
            ant_lon=ant_lon,
            ranges_m=payload["ranges_m"],
            azimuth_step_deg=payload["azimuth_step_deg"],
            max_range_m=payload["max_range_m"],
        )

        # Build merged polygon GeoJSON if in Polygon mode
        if self._shadow_mode == "Polygon":
            from src.shadow_builder import build_merged_shadow_geojson
            self._shadow_geojson = build_merged_shadow_geojson(
                ant_lat, ant_lon,
                self._shadow_segments_cache,
                payload["azimuth_step_deg"],
            )
        else:
            self._shadow_geojson = None

        features = self._build_coverage_features(self.coverage_data)
        self._render_map(ant_lat, ant_lon, features, self._shadow_features)
```

- [ ] **Step 3: Add GeoJSON injection to `_generate_leaflet_html()`**

In `_generate_leaflet_html()`, replace the `shadow_blocks` section (lines 288–298) with:

```python
        shadow_blocks = []

        if self._shadow_mode == "Polygon" and self._shadow_geojson:
            # Inject pre-built merged GeoJSON as L.geoJSON() layer
            geojson_str = json.dumps(self._shadow_geojson, separators=(',', ':'))
            shadow_blocks.append(f"""\
L.geoJSON({geojson_str}, {{
    style: {{
        color: '#8b0000',
        fillColor: '#8b0000',
        weight: 0,
        fillOpacity: {round(self._shadow_opacity, 4)}
    }},
    interactive: false
}}).addTo(map);""")
        else:
            # Wedge mode: individual 4-corner polygon per blocked azimuth
            for sfeat in (shadow_features or []):
                slatlngs_json = json.dumps(sfeat["latlngs"], separators=(',', ':'))
                shadow_blocks.append(f"""\
L.polygon({slatlngs_json}, {{
    color: '#8b0000',
    fillColor: '#8b0000',
    weight: 0,
    fillOpacity: {round(self._shadow_opacity, 4)},
    interactive: false
}}).addTo(map);""")
```

- [ ] **Step 4: Run the resolution/shadow tests**

```
python -m pytest tests/test_resolution_and_shadow.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add src/gui/map_view.py
git commit -m "feat: MapView adds continuous polygon shadow mode alongside existing wedge mode"
```

---

## Task 7: Add shadow mode toggle to `ControlPanel` and wire it in `MainWindow`

**Files:**
- Modify: `src/gui/control_panel.py`
- Modify: `src/gui/main_window.py`

- [ ] **Step 1: Add `shadow_mode_changed` signal to `ControlPanel`**

In `ControlPanel`, find the signals block (around line 73) and add:

```python
    shadow_mode_changed = pyqtSignal(str)  # "Wedge" or "Polygon"
```

- [ ] **Step 2: Add the shadow mode combo box to the Display group**

Find the Display group in the control panel (the group that contains the shadow opacity slider). After the shadow opacity row, add a new row:

```python
        # Shadow style toggle
        shadow_mode_layout = QHBoxLayout()
        shadow_mode_layout.addWidget(QLabel("Shadow style:"))
        self.combo_shadow_mode = QComboBox()
        self.combo_shadow_mode.addItem("Wedge",    userData="Wedge")
        self.combo_shadow_mode.addItem("Polygon (Smooth)", userData="Polygon")
        self.combo_shadow_mode.setToolTip(
            "Wedge: fast per-azimuth render.\n"
            "Polygon: shapely-merged smooth blobs (slower, better quality)."
        )
        self.combo_shadow_mode.currentIndexChanged.connect(self._on_shadow_mode_changed)
        shadow_mode_layout.addWidget(self.combo_shadow_mode)
        display_layout.addLayout(shadow_mode_layout)
```

Add the handler method to `ControlPanel`:

```python
    def _on_shadow_mode_changed(self, _index: int):
        mode = self.combo_shadow_mode.currentData()
        self.shadow_mode_changed.emit(mode)
```

- [ ] **Step 3: Connect `shadow_mode_changed` in `MainWindow._create_layout()`**

In `_create_layout()`, after line 486 (`self.control_panel.shadow_opacity_changed.connect(...)`), add:

```python
        self.control_panel.shadow_mode_changed.connect(self.map_view.set_shadow_mode)
```

- [ ] **Step 4: Smoke-test the app launches without error**

```
python main.py
```
Expected: app opens, Display section now shows "Shadow style: Wedge / Polygon (Smooth)" combo.

- [ ] **Step 5: Commit**

```
git add src/gui/control_panel.py src/gui/main_window.py
git commit -m "feat: shadow mode toggle (Wedge / Polygon Smooth) in Display panel"
```

---

## Task 8: Wire `fill_voids()` into `ComputationWorker` and display void %

**Files:**
- Modify: `src/gui/main_window.py`

- [ ] **Step 1: Add `void_pct_ready` signal to `ComputationWorker`**

In the `ComputationWorker` signals block (lines 41–48), add:

```python
    void_pct_ready = pyqtSignal(float)   # void fill percentage from DEMPreprocessor
```

- [ ] **Step 2: Replace the raw DEM load block with void-fill pipeline**

Replace lines 76–90 in `ComputationWorker.run()` (the `with rasterio.open(...)` block and the two `np.where` clamps) with:

```python
            # Load DEM and run tiered void-fill pipeline.
            # NOTE: fill_voids() handles nodata (-32768), <-500, >9000 internally.
            with rasterio.open(req.dem_path) as src:
                raw_data = src.read(1).astype(np.float64)
                transform = src.transform
                nodata    = src.nodata
                dem_bounds = src.bounds

            # Mark rasterio nodata sentinel as SRTM nodata so fill_voids detects it
            if nodata is not None and nodata != -32768:
                raw_data = np.where(raw_data == nodata, -32768.0, raw_data)

            from src.dem_preprocessor import fill_voids
            void_result = fill_voids(raw_data)
            dem_data    = void_result['filled']
            void_pct    = void_result['void_fraction'] * 100.0
            self.void_pct_ready.emit(void_pct)

            self.progress_update.emit(
                f"DEM loaded: {dem_data.shape[1]}×{dem_data.shape[0]} px  "
                f"void fill {void_pct:.1f}%  "
                f"bounds W{dem_bounds.left:.2f} E{dem_bounds.right:.2f} "
                f"S{dem_bounds.bottom:.2f} N{dem_bounds.top:.2f}"
            )
```

- [ ] **Step 3: Connect `void_pct_ready` in `MainWindow._on_compute_requested()`**

In `_on_compute_requested()`, after the line that connects `computation_timed` (line 576), add:

```python
        self.computation_worker.void_pct_ready.connect(self._on_void_pct_ready)
```

Add the handler method to `MainWindow`:

```python
    def _on_void_pct_ready(self, pct: float):
        """Update void fill label in status bar."""
        self.label_void_pct.setText(f"Void fill: {pct:.1f}%")
```

- [ ] **Step 4: Run Tier-1 tests (no DEM required — should still pass)**

```
python -m pytest tests/test_tier1_validation.py -v
```
Expected: V1–V5 PASS (Tier-1 tests use synthetic DEMs, not rasterio)

- [ ] **Step 5: Commit**

```
git add src/gui/main_window.py
git commit -m "feat: wire DEMPreprocessor fill_voids into worker; show void% in status bar"
```

---

## Task 9: Create `src/dem_manager.py` — SRTM tile auto-discovery and mosaic

**Files:**
- Create: `src/dem_manager.py`
- Create: `tests/test_dem_manager.py`

- [ ] **Step 1: Write `src/dem_manager.py`**

```python
# src/dem_manager.py
"""
DEM tile manager — SRTM3 tile name convention + rasterio multi-tile mosaic.

Responsibility:
  open_mosaic(dem_path, radar_lat, radar_lon, max_range_km)
    → (dem_array_float64, transform, bounds_namedtuple, n_tiles_used)

If only one tile covers the bounding box, it is returned directly.
If multiple tiles exist in the same directory, rasterio.merge stitches them.
If neighbor tiles are not found, the single specified file is used and a
warning is logged (cross-tile radials fall back to sea level, same as before).
"""

import logging
import numpy as np
from pathlib import Path

import rasterio
from rasterio.merge import merge as rio_merge
from rasterio.coords import BoundingBox
from rasterio.transform import array_bounds

logger = logging.getLogger(__name__)


def _srtm_tile_name(lat_floor: int, lon_floor: int) -> str:
    """
    Convert integer tile-corner coordinates to SRTM3 filename.

    E.g.: lat_floor=28, lon_floor=-17 → "N28W017.tif"
          lat_floor=-1, lon_floor=36   → "S01E036.tif"
    """
    ns = 'N' if lat_floor >= 0 else 'S'
    ew = 'E' if lon_floor >= 0 else 'W'
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}.tif"


def find_required_tiles(radar_lat: float, radar_lon: float, max_range_km: float) -> list[str]:
    """
    Return SRTM3 filenames for all 1°×1° tiles that intersect the bounding box
    defined by (radar_lat, radar_lon) ± max_range_km (plus 1° safety margin).

    Args:
        radar_lat, radar_lon: antenna WGS84 decimal degrees
        max_range_km:         maximum instrumented range in kilometres

    Returns:
        list[str]: SRTM3 filenames, e.g. ["N28W017.tif", "N28W016.tif", ...]
    """
    margin_deg = max_range_km / 111.0 + 1.0   # 111 km ≈ 1° latitude
    lat_min = int(np.floor(radar_lat - margin_deg))
    lat_max = int(np.floor(radar_lat + margin_deg))
    lon_min = int(np.floor(radar_lon - margin_deg))
    lon_max = int(np.floor(radar_lon + margin_deg))

    return [
        _srtm_tile_name(lat, lon)
        for lat in range(lat_min, lat_max + 1)
        for lon in range(lon_min, lon_max + 1)
    ]


def open_mosaic(
    dem_path: str,
    radar_lat: float,
    radar_lon: float,
    max_range_km: float,
) -> tuple:
    """
    Open DEM as a mosaic of all available SRTM tiles covering the bounding box.

    Searches the directory containing `dem_path` for any neighbour tiles
    matching the SRTM3 filename convention.  If none are found beside the
    explicitly selected file, that single file is used without mosaicking
    (same behaviour as before this function existed).

    Args:
        dem_path:      Path to the primary DEM GeoTIFF (user-selected via Browse DEM)
        radar_lat:     Antenna WGS84 latitude
        radar_lon:     Antenna WGS84 longitude
        max_range_km:  Maximum instrumented range (km) — defines bounding box

    Returns:
        (dem_array, transform, bounds, n_tiles)
        dem_array: float64 ndarray, raw elevations (nodata not yet filled)
        transform: rasterio Affine transform
        bounds:    rasterio BoundingBox namedtuple (left, bottom, right, top)
        n_tiles:   number of tiles merged (1 = single file, no mosaic)
    """
    dem_file = Path(dem_path)
    dem_dir  = dem_file.parent

    required = find_required_tiles(radar_lat, radar_lon, max_range_km)
    neighbours = [
        dem_dir / name
        for name in required
        if (dem_dir / name).exists() and (dem_dir / name) != dem_file
    ]

    if not neighbours:
        # Single-tile path (original behaviour)
        with rasterio.open(dem_file) as src:
            data = src.read(1).astype(np.float64)
            transform = src.transform
            bounds = src.bounds
        logger.info(f"DEM: single tile {dem_file.name}  ({len(required)} tiles required)")
        if len(required) > 1:
            logger.warning(
                f"{len(required) - 1} required tile(s) not found in {dem_dir} — "
                f"cross-tile radials will fall back to sea level (0 m)."
            )
        return data, transform, bounds, 1

    # Multi-tile mosaic
    all_paths = [dem_file] + neighbours
    logger.info(f"DEM mosaic: merging {len(all_paths)} tiles: {[p.name for p in all_paths]}")

    datasets = [rasterio.open(p) for p in all_paths]
    try:
        merged_array, merged_transform = rio_merge(
            datasets, nodata=-32768.0, dtype='float64'
        )
    finally:
        for ds in datasets:
            ds.close()

    dem_array = merged_array[0]   # single band
    h, w = dem_array.shape
    left, bottom, right, top = array_bounds(h, w, merged_transform)
    bounds = BoundingBox(left, bottom, right, top)

    return dem_array, merged_transform, bounds, len(all_paths)
```

- [ ] **Step 2: Write `tests/test_dem_manager.py`**

```python
# tests/test_dem_manager.py
"""
Tests for DEM tile manager — tile name convention and find_required_tiles.
These tests do NOT require real DEM files on disk.
"""
import pytest
from src.dem_manager import _srtm_tile_name, find_required_tiles


class TestSrtmTileName:
    def test_north_east(self):
        assert _srtm_tile_name(28, 14) == "N28E014.tif"

    def test_north_west(self):
        assert _srtm_tile_name(51, -1) == "N51W001.tif"

    def test_south_east(self):
        assert _srtm_tile_name(-1, 36) == "S01E036.tif"

    def test_south_west(self):
        assert _srtm_tile_name(-34, -70) == "S34W070.tif"

    def test_equator_prime_meridian(self):
        assert _srtm_tile_name(0, 0) == "N00E000.tif"


class TestFindRequiredTiles:
    def test_small_range_returns_at_least_one_tile(self):
        # 10 km range — must include the tile the radar sits in
        tiles = find_required_tiles(28.27, -16.64, max_range_km=10)
        assert "N28W017.tif" in tiles

    def test_large_range_covers_neighbors(self):
        # 200 km from Tenerife spans multiple 1° tiles
        tiles = find_required_tiles(28.27, -16.64, max_range_km=200)
        # Must span at least 3 lat rows × 3 lon columns
        assert len(tiles) >= 9

    def test_tile_names_follow_srtm_convention(self):
        tiles = find_required_tiles(51.5, 1.3, max_range_km=50)
        for name in tiles:
            assert name.endswith(".tif")
            # First char N or S, then 2 digits, then E or W, then 3 digits
            assert name[0] in ('N', 'S')
            assert name[3] in ('E', 'W')
            assert name[1:3].isdigit()
            assert name[4:7].isdigit()
```

- [ ] **Step 3: Run DEM manager tests**

```
python -m pytest tests/test_dem_manager.py -v
```
Expected: 8 PASSED

- [ ] **Step 4: Commit**

```
git add src/dem_manager.py tests/test_dem_manager.py
git commit -m "feat: DEMManager — SRTM tile auto-discovery and rasterio mosaic"
```

---

## Task 10: Wire `DEMManager` into `ComputationWorker`

**Files:**
- Modify: `src/gui/main_window.py`

- [ ] **Step 1: Replace the single-tile DEM open with `open_mosaic()`**

In `ComputationWorker.run()`, the DEM load block now reads (from Task 8):

```python
            with rasterio.open(req.dem_path) as src:
                raw_data = src.read(1).astype(np.float64)
                transform = src.transform
                nodata    = src.nodata
                dem_bounds = src.bounds
```

Replace those 6 lines with:

```python
            from src.dem_manager import open_mosaic
            raw_data, transform, dem_bounds, n_tiles = open_mosaic(
                req.dem_path, req.radar_lat, req.radar_lon, req.max_range_km
            )
            # raw_data is already float64; nodata (-32768) handled by fill_voids below
            nodata = None   # open_mosaic normalises to -32768 sentinel; fill_voids owns it
```

- [ ] **Step 2: Update the progress message to show tile count**

In the progress emit (the one that shows bounds), update `f"DEM loaded: ..."` to:

```python
            self.progress_update.emit(
                f"DEM loaded: {dem_data.shape[1]}×{dem_data.shape[0]} px  "
                f"{n_tiles} tile(s)  void fill {void_pct:.1f}%  "
                f"bounds W{dem_bounds.left:.2f} E{dem_bounds.right:.2f} "
                f"S{dem_bounds.bottom:.2f} N{dem_bounds.top:.2f}"
            )
```

- [ ] **Step 3: Run full test suite**

```
python -m pytest tests/ -v
```
Expected: all existing tests PASS; no regressions.

- [ ] **Step 4: Smoke-test app — compute on a single tile**

```
python main.py
```

Load any SRTM tile, compute, verify:
- Status bar shows "Void fill: X.X%"
- Progress bar message shows tile count
- Both Wedge and Polygon shadow modes render correctly

- [ ] **Step 5: Final commit**

```
git add src/gui/main_window.py
git commit -m "feat: wire DEMManager multi-tile mosaic into ComputationWorker"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Keep azimuth wedge approach → Task 5/6 preserve `_build_shadow_features` and `_shadow_features`; wedge is still the default mode
- [x] Add continuous polygon approach → Tasks 1-6 add `shadow_builder.py` + MapView polygon path
- [x] Shadow mode toggle → Task 7 adds `QComboBox` in Display group
- [x] Wire void fill pipeline → Task 8
- [x] Display void% in status bar → Task 8, `label_void_pct`
- [x] Multi-tile mosaic → Tasks 9-10

**Placeholder scan:** None found. All steps contain working code.

**Type consistency:**
- `extract_blocked_segments` → returns `list[tuple[float, float, float]]` — used identically in Tasks 2, 3, 5
- `build_merged_shadow_geojson` → returns `dict` GeoJSON FeatureCollection — injected as JSON string in Task 6
- `shadow_segments` key in payload dict → `list[tuple]` in Task 5, consumed as such in Task 6
- `open_mosaic` → returns `(ndarray, Affine, BoundingBox, int)` — destructured identically in Tasks 9 and 10
- `set_shadow_mode(mode: str)` — called from MainWindow with `currentData()` string, matches method signature
