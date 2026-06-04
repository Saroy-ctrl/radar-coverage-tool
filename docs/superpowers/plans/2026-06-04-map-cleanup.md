# Map Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace filled shadow wedges with thin radial lines, add concentric range rings, and add live coverage/shadow opacity sliders.

**Architecture:** All rendering changes live in `src/gui/map_view.py` (shadow geometry + HTML generation + opacity state). Two new `QSlider` widgets with signals are added to `src/gui/control_panel.py`, connected to `MapView` public methods in `src/gui/main_window.py`. No physics engine changes.

**Tech Stack:** PyQt6, Leaflet 1.9.4 (HTML string generation), pyproj Geod (geodetic point projection), pytest

---

## File Map

| File | Change |
|------|--------|
| `src/gui/map_view.py` | `_build_shadow_features()` → 2-point centerlines + filter; add `_coverage_opacity_factor`, `_shadow_opacity`, `_max_range_m`; add `set_coverage_opacity()`, `set_shadow_opacity()`; add `_generate_range_rings_js()`; update `_generate_leaflet_html()` and `set_shadow_data()` |
| `src/gui/control_panel.py` | Add `coverage_opacity_changed` / `shadow_opacity_changed` signals; add "Display" group with two sliders |
| `src/gui/main_window.py` | Connect two new signals to MapView in `_create_layout()` |
| `tests/test_resolution_and_shadow.py` | Update `test_build_shadow_features_fully_blocked_returns_features` (4→2 points); add significance filter test; add range ring interval tests |

---

## Task 1: Update `_build_shadow_features()` — 2-point centerlines + significance filter

**Files:**
- Modify: `src/gui/map_view.py` (method `_build_shadow_features`, lines ~161–214)
- Modify: `tests/test_resolution_and_shadow.py`

- [ ] **Step 1: Update the failing test for 4-point check → 2-point**

In `tests/test_resolution_and_shadow.py`, replace the body of `test_build_shadow_features_fully_blocked_returns_features`:

```python
def test_build_shadow_features_fully_blocked_returns_features():
    """All azimuths blocked at min range → one 2-point centerline per azimuth."""
    mv = _make_map_view()
    max_r = 100_000.0
    min_r = 200.0
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
        assert len(f["latlngs"]) == 2   # inner point + outer point
```

- [ ] **Step 2: Add significance-filter test**

Append to `tests/test_resolution_and_shadow.py`:

```python
def test_build_shadow_features_significance_filter():
    """Azimuths with inner_r >= 0.85 * max_range_m must be skipped."""
    mv = _make_map_view()
    max_r = 100_000.0
    # First 90 azimuths barely blocked (inner_r = 90 000 = 0.9 * max_r → skip)
    # Next 90 azimuths clearly blocked (inner_r = 50 000 = 0.5 * max_r → keep)
    ranges_m = [90_000.0] * 90 + [50_000.0] * 90
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=2.0,
        max_range_m=max_r,
    )
    assert len(feats) == 90   # only the clearly-blocked azimuths
```

- [ ] **Step 3: Run updated + new tests to confirm they fail**

```
python -m pytest tests/test_resolution_and_shadow.py::test_build_shadow_features_fully_blocked_returns_features tests/test_resolution_and_shadow.py::test_build_shadow_features_significance_filter -v
```

Expected: `FAILED` (existing method still returns 4-point polygons, no filter)

- [ ] **Step 4: Replace `_build_shadow_features()` in `src/gui/map_view.py`**

Find the method (around line 161) and replace its entire body:

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
    Build shadow centerline features for terrain-blocked azimuths.

    Returns one 2-point dict per blocked azimuth: the line from the
    coverage boundary to max_range along the azimuth centre.
    Azimuths where inner_r >= 0.85 * max_range_m are skipped
    (barely-blocked directions add visual noise without insight).
    """
    from pyproj import Geod
    GEOD = Geod(ellps='WGS84')
    significance_threshold = 0.85 * max_range_m
    features = []

    for i, inner_r in enumerate(ranges_m):
        if inner_r >= significance_threshold:
            continue

        az = i * azimuth_step_deg

        lon_inner, lat_inner, _ = GEOD.fwd(ant_lon, ant_lat, az, float(inner_r))
        lon_outer, lat_outer, _ = GEOD.fwd(ant_lon, ant_lat, az, max_range_m)

        features.append({
            "latlngs": [
                [float(lat_inner), float(lon_inner)],
                [float(lat_outer), float(lon_outer)],
            ]
        })

    return features
```

- [ ] **Step 5: Run both tests — expect PASS**

```
python -m pytest tests/test_resolution_and_shadow.py::test_build_shadow_features_fully_blocked_returns_features tests/test_resolution_and_shadow.py::test_build_shadow_features_significance_filter -v
```

Expected: both `PASSED`

- [ ] **Step 6: Run full test suite — no regressions**

```
python -m pytest tests/ -v
```

Expected: all tests pass. (Note: `test_build_shadow_features_latlngs_are_floats` uses `ranges_m=[50_000]*360` with `max_range_m=100_000` — 50 000 < 85 000 threshold so features ARE produced. That test still passes.)

- [ ] **Step 7: Commit**

```
git add src/gui/map_view.py tests/test_resolution_and_shadow.py
git commit -m "feat: shadow features as 2-point centerlines with 85% significance filter"
```

---

## Task 2: Update shadow rendering in HTML — L.polygon → L.polyline

**Files:**
- Modify: `src/gui/map_view.py` (method `_generate_leaflet_html`, shadow_blocks section)

- [ ] **Step 1: Write a test that checks the generated HTML uses L.polyline for shadows**

Append to `tests/test_resolution_and_shadow.py`:

```python
def test_shadow_rendered_as_polyline_not_polygon():
    """Shadow features must be rendered as L.polyline, not L.polygon."""
    mv = _make_map_view()
    # Manually set the instance attributes _generate_leaflet_html depends on
    mv._coverage_opacity_factor = 1.0
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0

    shadow_feats = [{"latlngs": [[51.6, 0.1], [51.8, 0.3]]}]
    html = mv._generate_leaflet_html(51.5, 0.0, [], shadow_feats)

    assert "L.polyline" in html
    assert "L.polygon" not in html or html.count("L.polygon") == 0
```

- [ ] **Step 2: Run test to confirm it fails**

```
python -m pytest tests/test_resolution_and_shadow.py::test_shadow_rendered_as_polyline_not_polygon -v
```

Expected: `FAILED` — `L.polygon` is still in the generated HTML

- [ ] **Step 3: Add `_coverage_opacity_factor`, `_shadow_opacity`, `_max_range_m` to `MapView.__init__`**

In `src/gui/map_view.py`, inside `MapView.__init__()`, after `self._shadow_features = []`, add:

```python
self._coverage_opacity_factor = 1.0   # multiplier for all band opacities
self._shadow_opacity = 0.5            # absolute opacity for shadow polylines
self._max_range_m = 0.0               # set from shadow payload; drives range rings
```

- [ ] **Step 4: Replace shadow rendering block in `_generate_leaflet_html()`**

Find the shadow_blocks loop (around line 232):

```python
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
```

Replace with:

```python
        shadow_blocks = []
        for sfeat in (shadow_features or []):
            slatlngs_json = json.dumps(sfeat["latlngs"], separators=(',', ':'))
            shadow_blocks.append(f"""\
L.polyline({slatlngs_json}, {{
    color: '#cc2200',
    weight: 1,
    opacity: {self._shadow_opacity},
    interactive: false
}}).addTo(map);""")
```

- [ ] **Step 5: Run test — expect PASS**

```
python -m pytest tests/test_resolution_and_shadow.py::test_shadow_rendered_as_polyline_not_polygon -v
```

Expected: `PASSED`

- [ ] **Step 6: Run full test suite**

```
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```
git add src/gui/map_view.py tests/test_resolution_and_shadow.py
git commit -m "feat: render shadow zones as thin polylines instead of filled wedges"
```

---

## Task 3: Add range rings

**Files:**
- Modify: `src/gui/map_view.py` — add `_generate_range_rings_js()`, update `set_shadow_data()`, update `_generate_leaflet_html()`

- [ ] **Step 1: Write tests for `_generate_range_rings_js()` interval logic**

Append to `tests/test_resolution_and_shadow.py`:

```python
def test_range_ring_interval_selection():
    """Auto-interval must match the table in the spec."""
    mv = _make_map_view()
    mv._coverage_opacity_factor = 1.0
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0

    cases = [
        (25_000.0,   5_000.0),
        (50_000.0,  10_000.0),
        (100_000.0, 20_000.0),
        (200_000.0, 50_000.0),
    ]
    for max_r, expected_interval in cases:
        js = mv._generate_range_rings_js(51.5, 0.0, max_r)
        # The first ring radius should equal the interval
        assert f"radius: {expected_interval:.0f}" in js, (
            f"max_range={max_r}: expected interval {expected_interval}, js={js[:200]}"
        )


def test_range_ring_js_empty_when_no_range():
    """Returns empty string when max_range_m is 0."""
    mv = _make_map_view()
    mv._coverage_opacity_factor = 1.0
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0

    js = mv._generate_range_rings_js(51.5, 0.0, 0.0)
    assert js == ""
```

- [ ] **Step 2: Run tests — expect FAIL**

```
python -m pytest tests/test_resolution_and_shadow.py::test_range_ring_interval_selection tests/test_resolution_and_shadow.py::test_range_ring_js_empty_when_no_range -v
```

Expected: `FAILED` — method does not exist yet

- [ ] **Step 3: Add `_generate_range_rings_js()` to `MapView` in `src/gui/map_view.py`**

Insert the new method after `_build_shadow_features()` (before `_generate_leaflet_html()`):

```python
def _generate_range_rings_js(self, lat: float, lon: float, max_range_m: float) -> str:
    """
    Generate Leaflet JS for concentric range rings with distance labels.

    Interval is auto-selected based on max_range_m:
        <=  25 km →  5 km
        <=  50 km → 10 km
        <= 100 km → 20 km
        >  100 km → 50 km

    Labels are placed at the east point of each ring via pyproj fwd.
    """
    if max_range_m <= 0:
        return ""

    from pyproj import Geod
    geod = Geod(ellps='WGS84')

    if max_range_m <= 25_000:
        interval_m = 5_000
    elif max_range_m <= 50_000:
        interval_m = 10_000
    elif max_range_m <= 100_000:
        interval_m = 20_000
    else:
        interval_m = 50_000

    blocks = []
    r = interval_m
    while r <= max_range_m:
        lon_e, lat_e, _ = geod.fwd(lon, lat, 90, r)
        label = f"{r / 1000:.0f} km" if r >= 1000 else f"{r:.0f} m"
        blocks.append(f"""L.circle([{lat:.6f}, {lon:.6f}], {{
    radius: {r:.0f},
    color: 'rgba(255,255,255,0.22)',
    weight: 1,
    fill: false,
    interactive: false
}}).addTo(map);
L.marker([{lat_e:.6f}, {lon_e:.6f}], {{
    icon: L.divIcon({{
        className: '',
        html: '<span style="color:rgba(255,255,255,0.6);font-size:10px;font-family:sans-serif;white-space:nowrap;text-shadow:0 0 3px #000">{label}</span>',
        iconAnchor: [0, 8]
    }}),
    interactive: false
}}).addTo(map);""")
        r += interval_m

    return "\n".join(blocks)
```

- [ ] **Step 4: Update `set_shadow_data()` to store `max_range_m`**

In `src/gui/map_view.py`, find `set_shadow_data()`. After `self._shadow_features = self._build_shadow_features(...)`, add:

```python
self._max_range_m = payload["max_range_m"]
```

- [ ] **Step 5: Call `_generate_range_rings_js()` in `_generate_leaflet_html()`**

Inside `_generate_leaflet_html()`, find the `return f"""<!DOCTYPE html>...` block.
After the `{polygons_js}` line in the `<script>` section, add a range rings section.

Find in the return string:

```
    // ── coverage polygons ───────────────────────────────────────────────
    {polygons_js}
  </script>
```

Replace with:

```
    // ── coverage polygons ───────────────────────────────────────────────
    {polygons_js}

    // ── range rings ──────────────────────────────────────────────────────
    {range_rings_js}
  </script>
```

And above the `return` statement, add:

```python
range_rings_js = self._generate_range_rings_js(lat, lon, self._max_range_m)
```

- [ ] **Step 6: Run tests — expect PASS**

```
python -m pytest tests/test_resolution_and_shadow.py::test_range_ring_interval_selection tests/test_resolution_and_shadow.py::test_range_ring_js_empty_when_no_range -v
```

Expected: both `PASSED`

- [ ] **Step 7: Run full test suite**

```
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 8: Commit**

```
git add src/gui/map_view.py tests/test_resolution_and_shadow.py
git commit -m "feat: add range rings with auto-interval and km labels to Leaflet map"
```

---

## Task 4: Opacity state + methods in MapView + apply in HTML

**Files:**
- Modify: `src/gui/map_view.py` — add `set_coverage_opacity()`, `set_shadow_opacity()`, apply factor in coverage rendering

- [ ] **Step 1: Write tests**

Append to `tests/test_resolution_and_shadow.py`:

```python
def test_coverage_opacity_factor_applied_in_html():
    """Coverage polygon opacity must be band_base * factor."""
    mv = _make_map_view()
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0
    mv._coverage_opacity_factor = 0.5   # half opacity

    # 50m AGL band has base opacity 0.45 → expected 0.225
    feats = [{"height_m": 50.0, "area_km2": 100.0,
               "latlngs": [[51.5, 0.0], [51.6, 0.1], [51.5, 0.2]]}]
    html = mv._generate_leaflet_html(51.5, 0.0, feats, [])

    # 0.45 * 0.5 = 0.225 — check it appears in the HTML
    assert "0.225" in html


def test_set_coverage_opacity_updates_factor():
    """set_coverage_opacity must update _coverage_opacity_factor."""
    mv = _make_map_view()
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0
    mv._shadow_features = []
    mv.coverage_data = {}
    mv.radar_lat = 51.5
    mv.radar_lon = 0.0
    # _render_map calls _tmp_path.write_text — stub it out
    mv._tmp_path = type('P', (), {'write_text': lambda self, *a, **k: None})()
    mv.web_engine = type('W', (), {'setUrl': lambda self, *a, **k: None})()

    mv.set_coverage_opacity(0.3)
    assert mv._coverage_opacity_factor == 0.3


def test_set_shadow_opacity_updates_value():
    """set_shadow_opacity must update _shadow_opacity."""
    mv = _make_map_view()
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0
    mv._shadow_features = []
    mv.coverage_data = {}
    mv.radar_lat = 51.5
    mv.radar_lon = 0.0
    mv._tmp_path = type('P', (), {'write_text': lambda self, *a, **k: None})()
    mv.web_engine = type('W', (), {'setUrl': lambda self, *a, **k: None})()

    mv.set_shadow_opacity(0.8)
    assert mv._shadow_opacity == 0.8
```

- [ ] **Step 2: Run tests — expect FAIL**

```
python -m pytest tests/test_resolution_and_shadow.py::test_coverage_opacity_factor_applied_in_html tests/test_resolution_and_shadow.py::test_set_coverage_opacity_updates_factor tests/test_resolution_and_shadow.py::test_set_shadow_opacity_updates_value -v
```

Expected: `FAILED` — methods not defined yet, opacity not applied

- [ ] **Step 3: Apply `_coverage_opacity_factor` in `_generate_leaflet_html()`**

In the coverage polygon rendering loop in `_generate_leaflet_html()`, find:

```python
            ci     = self._get_color_info(h)
            color  = ci["color"]
            opacity = ci["opacity"]
```

Change to:

```python
            ci      = self._get_color_info(h)
            color   = ci["color"]
            opacity = round(ci["opacity"] * self._coverage_opacity_factor, 4)
```

- [ ] **Step 4: Add `set_coverage_opacity()` and `set_shadow_opacity()` to `MapView`**

Add after `set_dem_bounds()` in `src/gui/map_view.py`:

```python
def set_coverage_opacity(self, factor: float):
    """Update coverage opacity multiplier and re-render. factor in [0.0, 1.0]."""
    self._coverage_opacity_factor = max(0.0, min(1.0, factor))
    features = self._build_coverage_features(self.coverage_data)
    self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

def set_shadow_opacity(self, opacity: float):
    """Update shadow line opacity and re-render. opacity in [0.0, 1.0]."""
    self._shadow_opacity = max(0.0, min(1.0, opacity))
    features = self._build_coverage_features(self.coverage_data)
    self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)
```

- [ ] **Step 5: Run tests — expect PASS**

```
python -m pytest tests/test_resolution_and_shadow.py::test_coverage_opacity_factor_applied_in_html tests/test_resolution_and_shadow.py::test_set_coverage_opacity_updates_factor tests/test_resolution_and_shadow.py::test_set_shadow_opacity_updates_value -v
```

Expected: all `PASSED`

- [ ] **Step 6: Run full test suite**

```
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```
git add src/gui/map_view.py tests/test_resolution_and_shadow.py
git commit -m "feat: opacity factor applied to coverage polygons; set_coverage_opacity and set_shadow_opacity methods"
```

---

## Task 5: Opacity sliders in ControlPanel

**Files:**
- Modify: `src/gui/control_panel.py`

No automated tests for this task — purely Qt widget construction. Verified in Task 6 manual run.

- [ ] **Step 1: Add two new signals to `ControlPanel` class**

In `src/gui/control_panel.py`, find the existing signal declarations:

```python
    compute_requested = pyqtSignal(ComputationRequest)
    load_dem_requested = pyqtSignal()
    load_obstructions_requested = pyqtSignal()
    export_geojson_requested = pyqtSignal()
```

Add two more:

```python
    coverage_opacity_changed = pyqtSignal(float)
    shadow_opacity_changed = pyqtSignal(float)
```

- [ ] **Step 2: Add slider widgets in `_create_widgets()`**

In `src/gui/control_panel.py`, at the end of `_create_widgets()` (before the closing of the method), add:

```python
        # === Display Group ===
        self.label_coverage_opacity = QLabel("Coverage opacity:")
        self.slider_coverage_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_coverage_opacity.setRange(0, 100)
        self.slider_coverage_opacity.setValue(100)
        self.label_coverage_opacity_val = QLabel("100%")
        self.label_coverage_opacity_val.setMinimumWidth(38)

        self.label_shadow_opacity = QLabel("Shadow opacity:")
        self.slider_shadow_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_shadow_opacity.setRange(0, 100)
        self.slider_shadow_opacity.setValue(50)
        self.label_shadow_opacity_val = QLabel("50%")
        self.label_shadow_opacity_val.setMinimumWidth(38)
```

- [ ] **Step 3: Add the "Display" group to `_create_layout()`**

In `src/gui/control_panel.py`, find the Computation Resolution group block:

```python
        # === Computation Resolution Group ===
        group_resolution = QGroupBox("Computation Resolution")
        ...
        layout.addWidget(group_resolution)

        # === Height Bands Group ===
```

Insert the new group between them:

```python
        # === Display Group ===
        group_display = QGroupBox("Display")
        gdisp_layout = QVBoxLayout()

        cov_row = QHBoxLayout()
        cov_row.addWidget(self.label_coverage_opacity)
        cov_row.addWidget(self.slider_coverage_opacity)
        cov_row.addWidget(self.label_coverage_opacity_val)
        gdisp_layout.addLayout(cov_row)

        shd_row = QHBoxLayout()
        shd_row.addWidget(self.label_shadow_opacity)
        shd_row.addWidget(self.slider_shadow_opacity)
        shd_row.addWidget(self.label_shadow_opacity_val)
        gdisp_layout.addLayout(shd_row)

        group_display.setLayout(gdisp_layout)
        layout.addWidget(group_display)
```

- [ ] **Step 4: Connect sliders in `_connect_signals()` and add handlers**

In `src/gui/control_panel.py`, at the end of `_connect_signals()`, add:

```python
        self.slider_coverage_opacity.valueChanged.connect(self._on_coverage_opacity_changed)
        self.slider_shadow_opacity.valueChanged.connect(self._on_shadow_opacity_changed)
```

Add the two handler methods after `_on_resolution_changed()`:

```python
    def _on_coverage_opacity_changed(self, value: int):
        """Emit coverage opacity as 0.0–1.0 fraction."""
        self.label_coverage_opacity_val.setText(f"{value}%")
        self.coverage_opacity_changed.emit(value / 100.0)

    def _on_shadow_opacity_changed(self, value: int):
        """Emit shadow opacity as 0.0–1.0 fraction."""
        self.label_shadow_opacity_val.setText(f"{value}%")
        self.shadow_opacity_changed.emit(value / 100.0)
```

- [ ] **Step 5: Commit**

```
git add src/gui/control_panel.py
git commit -m "feat: add coverage and shadow opacity sliders to control panel"
```

---

## Task 6: Wire signals in MainWindow + manual end-to-end test

**Files:**
- Modify: `src/gui/main_window.py` — two new `connect()` calls in `_create_layout()`

- [ ] **Step 1: Add connections in `_create_layout()`**

In `src/gui/main_window.py`, find the existing signal connections at the end of `_create_layout()`:

```python
        self.control_panel.compute_requested.connect(self._on_compute_requested)
        self.control_panel.load_dem_requested.connect(self._on_load_dem)
        self.control_panel.load_obstructions_requested.connect(self._on_load_obstructions)
        self.control_panel.export_geojson_requested.connect(self._on_export_geojson)
```

Add after:

```python
        self.control_panel.coverage_opacity_changed.connect(self.map_view.set_coverage_opacity)
        self.control_panel.shadow_opacity_changed.connect(self.map_view.set_shadow_opacity)
```

- [ ] **Step 2: Run full test suite**

```
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 3: Launch the app and verify manually**

```
python main.py
```

Checklist:
- [ ] App opens without errors
- [ ] Control panel shows a "Display" group with two sliders (Coverage opacity at 100%, Shadow opacity at 50%)
- [ ] Load DEM, set parameters, click Compute
- [ ] After computation: shadow areas show as thin red lines (not filled wedges)
- [ ] Range rings appear at the correct interval (e.g. 10 km rings for 50 km max range)
- [ ] Range ring labels show km values at the east side of each ring
- [ ] Drag coverage opacity to 0% → coverage polygons disappear
- [ ] Drag coverage opacity to 50% → all bands dimmed uniformly
- [ ] Drag coverage opacity back to 100% → bands at normal opacity
- [ ] Drag shadow opacity to 0% → shadow lines disappear
- [ ] Drag shadow opacity to 100% → shadow lines fully opaque
- [ ] Sliders take effect immediately without recomputing

- [ ] **Step 4: Commit**

```
git add src/gui/main_window.py
git commit -m "feat: wire coverage and shadow opacity signals to MapView"
```

---

## Done

All three spec requirements implemented:
1. Shadow zones → thin `weight:1` red polylines with 85% significance filter
2. Range rings → auto-interval concentric circles with east-side km labels
3. Opacity sliders → live coverage × factor and shadow absolute opacity, no recompute needed
