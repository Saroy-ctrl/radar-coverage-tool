# Resolution Presets & Terrain-Blocking Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four named resolution presets to the control panel and render terrain-blocked (shadow) zones as dark-red radial wedge polygons on the Leaflet map, matching Cambridge Pixel SPx visual fidelity.

**Architecture:** `ComputationRequest` gains two new fields (`azimuth_step_deg`, `range_step_m`) that the worker reads instead of hardcoded constants. The worker emits two new signals — `shadow_data_ready` (lowest-band blocking ranges) and `computation_timed` (elapsed seconds) — which `MainWindow` routes to `MapView.set_shadow_data()` and `ControlPanel.recalibrate_estimate()`. `MapView` builds dark-red wedge polygons from the shadow data and injects them before coverage polygons in the Leaflet HTML.

**Tech Stack:** PyQt6, NumPy, pyproj (Geod.fwd), Leaflet 1.9.4 (via QWebEngineView)

---

## File Map

| File | Change |
|------|--------|
| `src/gui/control_panel.py` | Extend `ComputationRequest`; add resolution group widget + `recalibrate_estimate()` |
| `src/gui/main_window.py` | Replace hardcoded constants; add 2 new signals; emit shadow + timing data |
| `src/gui/map_view.py` | Add `set_shadow_data()`, `_build_shadow_features()`, shadow rendering in HTML |
| `tests/test_resolution_and_shadow.py` | New test file covering data-layer functions |

---

## Task 1: Extend `ComputationRequest` and add resolution preset widget

**Files:**
- Modify: `src/gui/control_panel.py`
- Create: `tests/test_resolution_and_shadow.py`

### Step 1a: Write failing tests for resolution preset logic

- [ ] Create `tests/test_resolution_and_shadow.py`:

```python
"""Tests for resolution preset mapping and ComputationRequest fields."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.gui.control_panel import ComputationRequest, RESOLUTION_PRESETS


def test_computation_request_has_resolution_fields():
    req = ComputationRequest(
        radar_lat=51.0, radar_lon=0.0,
        site_elevation_amsl_m=100.0, antenna_amsl_m=120.0,
        k_factor=1.333, max_range_km=200.0,
        height_bands_m=[50.0, 100.0],
        diffraction_guard_deg=0.5,
        azimuth_step_deg=1.0,
        range_step_m=100.0,
    )
    assert req.azimuth_step_deg == 1.0
    assert req.range_step_m == 100.0


def test_computation_request_default_resolution():
    """Default resolution must be Fast preset (2°/200m)."""
    req = ComputationRequest(
        radar_lat=51.0, radar_lon=0.0,
        site_elevation_amsl_m=100.0, antenna_amsl_m=120.0,
        k_factor=1.333, max_range_km=200.0,
        height_bands_m=[50.0],
        diffraction_guard_deg=0.5,
    )
    assert req.azimuth_step_deg == 2.0
    assert req.range_step_m == 200.0


def test_resolution_presets_keys():
    assert set(RESOLUTION_PRESETS.keys()) == {"Fast", "Standard", "High", "Ultra"}


def test_resolution_presets_values():
    # Each preset: (azimuth_step_deg, range_step_m)
    assert RESOLUTION_PRESETS["Fast"]     == (2.0,  200.0)
    assert RESOLUTION_PRESETS["Standard"] == (1.0,  100.0)
    assert RESOLUTION_PRESETS["High"]     == (0.5,  100.0)
    assert RESOLUTION_PRESETS["Ultra"]    == (0.5,   50.0)
```

- [ ] Run test to confirm it fails:

```
python -m pytest tests/test_resolution_and_shadow.py -v
```
Expected: `ImportError` or `AttributeError` — `RESOLUTION_PRESETS` not yet defined.

### Step 1b: Add `RESOLUTION_PRESETS` constant and new `ComputationRequest` fields

- [ ] In `src/gui/control_panel.py`, add `RESOLUTION_PRESETS` after `HEIGHT_BANDS`:

```python
# (azimuth_step_deg, range_step_m)
RESOLUTION_PRESETS = {
    "Fast":     (2.0,  200.0),
    "Standard": (1.0,  100.0),
    "High":     (0.5,  100.0),
    "Ultra":    (0.5,   50.0),
}

RESOLUTION_ESTIMATES = {
    "Fast":     "~30s",
    "Standard": "~2 min",
    "High":     "~4 min",
    "Ultra":    "~8 min",
}
```

- [ ] Extend `ComputationRequest` dataclass (add the two new fields **at the end** so existing callers that use positional args are unaffected):

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
```

- [ ] Run tests to confirm they pass:

```
python -m pytest tests/test_resolution_and_shadow.py::test_computation_request_has_resolution_fields tests/test_resolution_and_shadow.py::test_computation_request_default_resolution tests/test_resolution_and_shadow.py::test_resolution_presets_keys tests/test_resolution_and_shadow.py::test_resolution_presets_values -v
```
Expected: 4 PASS.

### Step 1c: Add resolution widget to `ControlPanel._create_widgets()`

- [ ] Inside `_create_widgets()`, after the diffraction guard block (around line 130), add:

```python
        # === Computation Resolution Group ===
        self.label_resolution = QLabel("Computation Resolution:")
        self.combo_resolution = QComboBox()
        for name, _ in RESOLUTION_PRESETS.items():
            self.combo_resolution.addItem(f"{name}  ({RESOLUTION_ESTIMATES[name]})")
        self.combo_resolution.setCurrentIndex(0)  # Fast by default

        self.label_resolution_estimate = QLabel(f"Est. {RESOLUTION_ESTIMATES['Fast']}")
        self.label_resolution_estimate.setStyleSheet("color: #aaa; font-size: 11px;")

        # Per-preset measured elapsed time (recalibrated after each run)
        self._measured_elapsed = {}
```

### Step 1d: Add resolution group to `_create_layout()`

- [ ] In `_create_layout()`, add the resolution group **after** the Radar Parameters group block (after `layout.addWidget(group_radar)`):

```python
        # === Computation Resolution Group ===
        group_resolution = QGroupBox("Computation Resolution")
        gres_layout = QVBoxLayout()
        gres_layout.addWidget(self.label_resolution)
        res_row = QHBoxLayout()
        res_row.addWidget(self.combo_resolution)
        res_row.addWidget(self.label_resolution_estimate)
        gres_layout.addLayout(res_row)
        group_resolution.setLayout(gres_layout)
        layout.addWidget(group_resolution)
```

### Step 1e: Connect combo signal and add helper + recalibrate method

- [ ] In `_connect_signals()`, add:

```python
        self.combo_resolution.currentIndexChanged.connect(self._on_resolution_changed)
```

- [ ] Add these two methods to `ControlPanel`:

```python
    def _on_resolution_changed(self, index: int):
        """Update estimate label when resolution preset changes."""
        name = list(RESOLUTION_PRESETS.keys())[index]
        if name in self._measured_elapsed:
            secs = self._measured_elapsed[name]
            if secs < 60:
                label = f"~{secs:.0f}s"
            else:
                label = f"~{secs/60:.1f} min"
        else:
            label = RESOLUTION_ESTIMATES[name]
        self.label_resolution_estimate.setText(f"Est. {label}")

    def recalibrate_estimate(self, elapsed_seconds: float):
        """Store measured elapsed time for the preset just used."""
        index = self.combo_resolution.currentIndex()
        name = list(RESOLUTION_PRESETS.keys())[index]
        self._measured_elapsed[name] = elapsed_seconds
        self._on_resolution_changed(index)

    def _get_resolution_params(self) -> tuple[float, float]:
        """Return (azimuth_step_deg, range_step_m) for current preset."""
        index = self.combo_resolution.currentIndex()
        name = list(RESOLUTION_PRESETS.keys())[index]
        return RESOLUTION_PRESETS[name]
```

### Step 1f: Update `_on_compute_clicked()` to include resolution fields

- [ ] In `_on_compute_clicked()`, replace the `request = ComputationRequest(...)` block with:

```python
        az_step, rng_step = self._get_resolution_params()
        request = ComputationRequest(
            radar_lat=self.spin_lat.value(),
            radar_lon=self.spin_lon.value(),
            site_elevation_amsl_m=float(site_elev),
            antenna_amsl_m=float(site_elev + self.spin_mast_height.value() + self.spin_antenna_height.value()),
            k_factor=self.slider_k_factor.value() / 100.0,
            max_range_km=self.spin_max_range.value(),
            height_bands_m=self._get_selected_height_bands(),
            diffraction_guard_deg=self.slider_diffraction.value() * 0.1,
            dem_path=self.dem_path,
            obstructions_path=self.obstructions_path,
            azimuth_step_deg=az_step,
            range_step_m=rng_step,
        )
```

### Step 1g: Commit

- [ ] Run existing tier-1 tests to confirm nothing broke:

```
python -m pytest tests/test_tier1_validation.py -v
```
Expected: all PASS (these tests don't use `ComputationRequest` directly).

- [ ] Commit:

```bash
git add src/gui/control_panel.py tests/test_resolution_and_shadow.py
git commit -m "feat: add resolution presets to control panel and ComputationRequest"
```

---

## Task 2: Replace hardcoded constants and add new signals in `ComputationWorker`

**Files:**
- Modify: `src/gui/main_window.py`
- Modify: `tests/test_resolution_and_shadow.py`

### Step 2a: Write failing test for shadow data structure

- [ ] Append to `tests/test_resolution_and_shadow.py`:

```python
def test_shadow_data_payload_structure():
    """shadow_data_ready payload must have ranges_m, azimuth_step_deg, max_range_m."""
    payload = {
        "ranges_m": [50000.0, 60000.0, 100000.0],
        "azimuth_step_deg": 2.0,
        "max_range_m": 100000.0,
    }
    assert isinstance(payload["ranges_m"], list)
    assert isinstance(payload["azimuth_step_deg"], float)
    assert isinstance(payload["max_range_m"], float)
    assert all(r >= 0 for r in payload["ranges_m"])
```

This test documents the contract — it passes trivially, which is intentional for a data-shape test.

- [ ] Run to confirm it passes:

```
python -m pytest tests/test_resolution_and_shadow.py::test_shadow_data_payload_structure -v
```
Expected: PASS.

### Step 2b: Add new signals to `ComputationWorker`

- [ ] In `src/gui/main_window.py`, in the `ComputationWorker` class signal block (around line 41), add two signals after `computation_finished`:

```python
    shadow_data_ready = pyqtSignal(dict)    # blocking range per azimuth for lowest height band
    computation_timed = pyqtSignal(float)   # elapsed wall-clock seconds for this run
```

### Step 2c: Replace hardcoded constants and add timing

- [ ] In `ComputationWorker.run()`, replace:

```python
            RANGE_STEP_M = 200.0
            AZIMUTH_STEP = 2  # degrees
```

with:

```python
            RANGE_STEP_M = req.range_step_m
            AZIMUTH_STEP = req.azimuth_step_deg
```

- [ ] Wrap the azimuth loop with timing. Find the line:

```python
            self.progress_update.emit("Computing coverage (this may take a minute)...")
```

and add `import time` at the top of `run()` (after the other imports), then record start time:

```python
            import time as _time
            _t_start = _time.monotonic()
```

- [ ] After the azimuth loop (`if i % 20 == 0:` block ends), add:

```python
            _elapsed = _time.monotonic() - _t_start
```

- [ ] After `self.progress_update.emit("Building coverage polygons...")`, before building `coverage_data`, emit timing:

```python
            self.computation_timed.emit(_elapsed)
```

### Step 2d: Emit `shadow_data_ready` with lowest height band ranges

- [ ] After `self.computation_timed.emit(_elapsed)`, add:

```python
            # Shadow data: lowest enabled height band, raw ranges in metres
            if heights_agl:
                _lowest_h = min(heights_agl)
                shadow_payload = {
                    "ranges_m": coverage_ranges_m[_lowest_h].tolist(),
                    "azimuth_step_deg": float(AZIMUTH_STEP),
                    "max_range_m": float(max_range_m),
                }
                self.shadow_data_ready.emit(shadow_payload)
```

### Step 2e: Verify the worker still runs end-to-end

- [ ] Run tier-1 tests:

```
python -m pytest tests/test_tier1_validation.py -v
```
Expected: all PASS.

- [ ] Commit:

```bash
git add src/gui/main_window.py tests/test_resolution_and_shadow.py
git commit -m "feat: use resolution from request in worker, emit shadow_data_ready and computation_timed"
```

---

## Task 3: Connect new signals in `MainWindow`

**Files:**
- Modify: `src/gui/main_window.py`

### Step 3a: Connect `shadow_data_ready` and `computation_timed`

- [ ] In `MainWindow._on_compute_requested()`, in the worker signal connection block (around lines 548–552), add two new connections after the existing ones:

```python
        self.computation_worker.shadow_data_ready.connect(self._on_shadow_data_ready)
        self.computation_worker.computation_timed.connect(self._on_computation_timed)
```

### Step 3b: Add the two new slot methods to `MainWindow`

- [ ] Add these methods to `MainWindow` (place them after `_on_polar_data_ready`):

```python
    def _on_shadow_data_ready(self, payload: dict):
        """Forward shadow blocking data to MapView."""
        req = self.computation_request
        if req is None:
            return
        self.map_view.set_shadow_data(req.radar_lat, req.radar_lon, payload)

    def _on_computation_timed(self, elapsed_seconds: float):
        """Recalibrate control panel estimate label with measured time."""
        self.control_panel.recalibrate_estimate(elapsed_seconds)
```

### Step 3c: Verify app starts without errors

- [ ] Launch the app and confirm the window opens without a traceback:

```
python main.py
```
Expected: window opens, no errors in terminal.

- [ ] Commit:

```bash
git add src/gui/main_window.py
git commit -m "feat: connect shadow_data_ready and computation_timed signals in MainWindow"
```

---

## Task 4: Shadow/blocking visualization in `MapView`

**Files:**
- Modify: `src/gui/map_view.py`
- Modify: `tests/test_resolution_and_shadow.py`

### Step 4a: Write failing tests for `_build_shadow_features`

- [ ] Append to `tests/test_resolution_and_shadow.py`:

```python
import numpy as np
from unittest.mock import patch


def _make_map_view():
    """Instantiate MapView without a display (headless)."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    from src.gui.map_view import MapView
    return MapView()


def test_build_shadow_features_full_coverage_no_shadow():
    """When every azimuth reaches max range, no shadow features should be produced."""
    mv = _make_map_view()
    max_r = 100_000.0
    ranges_m = [max_r] * 180          # all azimuths at full range
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=2.0,
        max_range_m=max_r,
    )
    assert feats == [], f"Expected no shadow features, got {len(feats)}"


def test_build_shadow_features_fully_blocked_returns_features():
    """When all azimuths are blocked at min range, features should be produced for every azimuth."""
    mv = _make_map_view()
    max_r = 100_000.0
    min_r = 200.0   # RANGE_STEP_M minimum
    ranges_m = [min_r] * 180
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=2.0,
        max_range_m=max_r,
    )
    assert len(feats) == 180
    for f in feats:
        assert "latlngs" in f
        assert len(f["latlngs"]) == 4


def test_build_shadow_features_latlngs_are_floats():
    """Each wedge latlngs should be [[lat, lon], ...] with float values."""
    mv = _make_map_view()
    ranges_m = [50_000.0] * 360
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=1.0,
        max_range_m=100_000.0,
    )
    assert len(feats) > 0
    for f in feats:
        for coord in f["latlngs"]:
            assert len(coord) == 2
            lat, lon = coord
            assert isinstance(lat, float)
            assert isinstance(lon, float)
            assert -90 <= lat <= 90
            assert -180 <= lon <= 180
```

- [ ] Run to confirm they fail:

```
python -m pytest tests/test_resolution_and_shadow.py::test_build_shadow_features_full_coverage_no_shadow tests/test_resolution_and_shadow.py::test_build_shadow_features_fully_blocked_returns_features tests/test_resolution_and_shadow.py::test_build_shadow_features_latlngs_are_floats -v
```
Expected: `AttributeError: 'MapView' object has no attribute '_build_shadow_features'`

### Step 4b: Add `_build_shadow_features` to `MapView`

- [ ] In `src/gui/map_view.py`, add this method inside `MapView` after `_build_coverage_features`:

```python
    def _build_shadow_features(
        self,
        ant_lat: float,
        ant_lon: float,
        ranges_m: list,
        azimuth_step_deg: float,
        max_range_m: float,
    ) -> list:
        """
        Build dark-red shadow wedge polygons for terrain-blocked zones.

        For each azimuth, the shadow spans from the max visible range
        (coverage boundary) to max_range_m. Each wedge is a 4-point
        geodetic polygon one azimuth-step wide.

        Args:
            ant_lat, ant_lon: antenna WGS84 position
            ranges_m:         per-azimuth max visible range (lowest height band)
            azimuth_step_deg: azimuth resolution used in this run
            max_range_m:      instrumented range limit

        Returns:
            list of {"latlngs": [[lat, lon], ...]} dicts (4 points each)
        """
        import numpy as np
        from pyproj import Geod
        GEOD = Geod(ellps='WGS84')
        half = azimuth_step_deg / 2.0
        features = []

        for i, inner_r in enumerate(ranges_m):
            outer_r = max_range_m
            if inner_r >= outer_r:
                continue   # no shadow: fully visible to max range

            az = i * azimuth_step_deg
            az_left  = az - half
            az_right = az + half

            # 4 corners of the wedge: inner-left, outer-left, outer-right, inner-right
            corners_az = [az_left,  az_left,  az_right, az_right]
            corners_r  = [inner_r,  outer_r,  outer_r,  inner_r]

            lons, lats, _ = GEOD.fwd(
                [ant_lon] * 4,
                [ant_lat] * 4,
                corners_az,
                corners_r,
            )
            features.append({
                "latlngs": [[float(lat), float(lon)]
                            for lat, lon in zip(lats, lons)]
            })

        return features
```

- [ ] Run the three tests:

```
python -m pytest tests/test_resolution_and_shadow.py::test_build_shadow_features_full_coverage_no_shadow tests/test_resolution_and_shadow.py::test_build_shadow_features_fully_blocked_returns_features tests/test_resolution_and_shadow.py::test_build_shadow_features_latlngs_are_floats -v
```
Expected: 3 PASS.

### Step 4c: Add `set_shadow_data` method and shadow state to `MapView`

- [ ] In `MapView.__init__`, add two attributes after `self.coverage_data = {}`:

```python
        self._shadow_features = []      # pre-built wedge list, rebuilt on set_shadow_data
```

- [ ] Add `set_shadow_data` to the public API section of `MapView`:

```python
    def set_shadow_data(self, ant_lat: float, ant_lon: float, payload: dict):
        """
        Rebuild and store shadow wedge features, then re-render the map.

        Args:
            ant_lat, ant_lon: antenna WGS84 position
            payload: {"ranges_m": list, "azimuth_step_deg": float, "max_range_m": float}
        """
        self._shadow_features = self._build_shadow_features(
            ant_lat=ant_lat,
            ant_lon=ant_lon,
            ranges_m=payload["ranges_m"],
            azimuth_step_deg=payload["azimuth_step_deg"],
            max_range_m=payload["max_range_m"],
        )
        # Re-render map with current coverage + new shadow
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)
```

### Step 4d: Update `_render_map` and `_generate_leaflet_html` to accept shadow features

- [ ] Update `_render_map` signature and body:

```python
    def _render_map(self, lat: float, lon: float,
                    coverage_features: list, shadow_features: list = None):
        """
        Write a fresh Leaflet HTML file and load it in the WebEngine.
        Shadow features are rendered first (underneath coverage polygons).
        """
        html = self._generate_leaflet_html(lat, lon, coverage_features,
                                           shadow_features or [])
        self._tmp_path.write_text(html, encoding="utf-8")
        self.web_engine.setUrl(QUrl("about:blank"))
        self.web_engine.setUrl(QUrl.fromLocalFile(str(self._tmp_path)))
```

- [ ] Update `update_coverage` to pass shadow features through:

```python
    def update_coverage(self, coverage_data: dict):
        """Update map with new coverage polygons."""
        self.coverage_data = coverage_data
        features = self._build_coverage_features(coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)
```

- [ ] Update `set_antenna_location` to pass shadow features through:

```python
    def set_antenna_location(self, lat: float, lon: float):
        """Update antenna location marker, preserving existing coverage and shadow."""
        self.radar_lat = lat
        self.radar_lon = lon
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(lat, lon, features, self._shadow_features)
```

### Step 4e: Render shadow wedges in `_generate_leaflet_html`

- [ ] Update `_generate_leaflet_html` signature:

```python
    def _generate_leaflet_html(self, lat: float, lon: float,
                               coverage_features: list,
                               shadow_features: list = None) -> str:
```

- [ ] Inside `_generate_leaflet_html`, after the `polygon_blocks` list is built for coverage, add shadow block generation **before** coverage — find the line:

```python
        polygon_blocks = []
        for feat in coverage_features:
```

and prepend a shadow section before it:

```python
        # Shadow polygons (rendered first — underneath coverage)
        shadow_blocks = []
        for sfeat in (shadow_features or []):
            slatlngs_json = json.dumps(sfeat["latlngs"], separators=(',', ':'))
            shadow_blocks.append(f"""\
L.polygon({slatlngs_json}, {{
    color: '#8b0000',
    fillColor: '#8b0000',
    weight: 0,
    opacity: 0,
    fillOpacity: 0.55
}}).addTo(map);""")

        polygon_blocks = []
        for feat in coverage_features:
```

- [ ] Update the JS injection in the return string. Find the line with:

```python
        polygons_js = "\n        ".join(polygon_blocks)
```

and replace it with:

```python
        shadows_js  = "\n        ".join(shadow_blocks)
        polygons_js = "\n        ".join(polygon_blocks)
```

- [ ] In the HTML template's `<script>` section, find:

```python
    // ── coverage polygons ───────────────────────────────────────────────
    {polygons_js}
```

and replace with:

```python
    // ── shadow (terrain-blocked) zones ─────────────────────────────────
    {shadows_js}

    // ── coverage polygons ───────────────────────────────────────────────
    {polygons_js}
```

### Step 4f: Run all tests

- [ ] Run full test suite:

```
python -m pytest tests/ -v
```
Expected: all PASS (tier-1 validation + new resolution/shadow tests).

### Step 4g: Smoke-test in the app

- [ ] Launch the app:

```
python main.py
```

- [ ] Load a DEM (any SRTM tile in `data/`), set resolution to "Fast", click Compute.
- [ ] Confirm: dark-red shadow spikes appear on the map beneath the coverage polygons.
- [ ] Switch to "Standard" or "High", re-compute, confirm finer polygon edges.
- [ ] After a run completes, confirm the estimate label updates (e.g., "Est. ~47s").

### Step 4h: Commit

```bash
git add src/gui/map_view.py tests/test_resolution_and_shadow.py
git commit -m "feat: terrain-blocking shadow visualization on Leaflet map"
```

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - Resolution presets (4 named, Fast/Standard/High/Ultra) → Task 1
  - `ComputationRequest` new fields `azimuth_step_deg`, `range_step_m` → Task 1
  - Hardcoded constants removed from worker → Task 2
  - `shadow_data_ready` signal (lowest height band, raw ranges) → Task 2
  - `computation_timed` signal → Task 2
  - Live estimate label updates → Task 1 (`_on_resolution_changed`)
  - Post-run recalibration → Task 1 (`recalibrate_estimate`) + Task 3 (`_on_computation_timed`)
  - Shadow wedges rendered before coverage polygons → Task 4
  - Lowest height band only for shadow → Task 2 (`min(heights_agl)`)
  - Shadow color `#8b0000`, opacity 0.55 → Task 4
  - No physics engine changes → confirmed, none of Tasks 1–4 touch `earth_model`, `visibility_engine`, `obstruction_engine`
  - Polar view unchanged → confirmed

- [x] **Placeholder scan:** All steps contain complete code. No TBDs.

- [x] **Type consistency:**
  - `shadow_data_ready` payload shape defined in Task 2d and consumed identically in Task 4c
  - `_build_shadow_features` signature defined in Task 4b and called identically in Task 4c
  - `_render_map(lat, lon, coverage_features, shadow_features)` updated in Task 4d; all 3 callers (`update_coverage`, `set_antenna_location`, `set_shadow_data`) pass `self._shadow_features` consistently
  - `recalibrate_estimate(elapsed_seconds: float)` defined in Task 1e, called in Task 3b
