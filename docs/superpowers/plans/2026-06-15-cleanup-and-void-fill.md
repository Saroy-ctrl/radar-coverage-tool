# Cleanup & Void-Fill Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the timing/results display clutter from the GUI, make Ultra the default resolution, wire DEMPreprocessor.fill_voids behind a removable flag, and repair 3 stale tests whose assertions no longer match the current shadow implementation.

**Architecture:** Four independent changes: (1) delete computation-timing signals and result labels from worker + status bar + control panel; (2) flip the default resolution combo index + dataclass defaults; (3) add a module-level `VOID_FILL_ENABLED` flag that gates a `fill_voids()` call immediately after DEM load in the worker; (4) update three test assertions to match current 4-corner wedge polygon behaviour.

**Tech Stack:** PyQt6, numpy, rasterio, pyproj, pytest. Entry point: `python main.py`. Tests: `pytest tests/ -v`.

---

## File Map

| File | What changes |
|------|-------------|
| `src/gui/main_window.py` | Remove `computation_timed` signal + timing code from `ComputationWorker`; remove `label_coverage_area`, `label_void_pct`, `_on_computation_timed` from `MainWindow`; add `VOID_FILL_ENABLED` flag + call `fill_voids()` in worker |
| `src/gui/control_panel.py` | Remove `label_computing`, `label_resolution_estimate`, `_measured_elapsed`, `recalibrate_estimate()`, `_on_resolution_changed()`; change default combo index to Ultra (3); change `ComputationRequest` defaults to Ultra (0.5 / 50.0) |
| `tests/test_resolution_and_shadow.py` | Update `test_computation_request_default_resolution` to Ultra values; fix 3 stale shadow test assertions |

---

## Task 1: Remove the computation-result timing system from the worker

**Files:**
- Modify: `src/gui/main_window.py`

This task removes all elapsed-time tracking from `ComputationWorker` and its signal from `MainWindow`.

**Exact lines to delete or edit:**

| What | Location in current file |
|------|--------------------------|
| `computation_timed = pyqtSignal(float)` signal declaration | line 49 |
| `_t_start = _time.monotonic()` | line 171 |
| `_elapsed = _time.monotonic() - _t_start` | line 264 |
| `self.computation_timed.emit(_elapsed)` | line 267 |
| `self.computation_worker.computation_timed.connect(self._on_computation_timed)` | line 658 |
| `_on_computation_timed` method + `recalibrate_estimate` call inside it | lines 705–707 |

- [ ] **Step 1: Delete `computation_timed` signal from `ComputationWorker`**

  In `src/gui/main_window.py` remove only this line from the class body:
  ```python
  computation_timed = pyqtSignal(float)   # elapsed wall-clock seconds for this run
  ```

- [ ] **Step 2: Delete timing variables from `ComputationWorker.run()`**

  Remove these three lines (they are standalone; removing them leaves no gap in logic):
  ```python
  _t_start = _time.monotonic()
  ```
  and later:
  ```python
  _elapsed = _time.monotonic() - _t_start
  ```
  and:
  ```python
  self.computation_timed.emit(_elapsed)
  ```
  Also remove the `self.progress_update.emit("Building coverage polygons...")` line that sits between the loop and the `self.computation_timed.emit()` call — keep only the shadow payload block that follows.
  
  Wait — keep `self.progress_update.emit("Building coverage polygons...")` since it's useful UX. Only remove the three timing lines.

- [ ] **Step 3: Disconnect the signal in `MainWindow._on_compute_requested`**

  Remove this single line from `_on_compute_requested`:
  ```python
  self.computation_worker.computation_timed.connect(self._on_computation_timed)
  ```

- [ ] **Step 4: Delete `_on_computation_timed` from `MainWindow`**

  Remove the entire method:
  ```python
  def _on_computation_timed(self, elapsed_seconds: float):
      """Recalibrate control panel estimate label with measured time."""
      self.control_panel.recalibrate_estimate(elapsed_seconds)
  ```

- [ ] **Step 5: Run tests to verify nothing broke**

  ```
  pytest tests/ -v -k "not tier1"
  ```
  Expected: all previously-passing tests still pass (no `computation_timed` attribute errors).

- [ ] **Step 6: Commit**

  ```
  git add src/gui/main_window.py
  git commit -m "refactor: remove computation_timed signal and elapsed-time tracking from worker"
  ```

---

## Task 2: Remove result labels from MainWindow status bar

**Files:**
- Modify: `src/gui/main_window.py`

Removes `label_coverage_area` and `label_void_pct` from `_create_status_bar` and the update call in `_on_coverage_computed`.

- [ ] **Step 1: Remove labels from `_create_status_bar`**

  Delete these six lines from `_create_status_bar`:
  ```python
  # Coverage area label
  self.label_coverage_area = QLabel("Coverage area: — km²")
  self.status_bar.addWidget(self.label_coverage_area)

  # Void percentage label
  self.label_void_pct = QLabel("Void fill: — %")
  self.status_bar.addWidget(self.label_void_pct)
  ```

- [ ] **Step 2: Remove area calculation from `_on_coverage_computed`**

  Delete the entire `if coverage_data:` block that computes `area_km2` and calls `self.label_coverage_area.setText(...)`. The handler becomes simply:
  ```python
  def _on_coverage_computed(self, coverage_data: dict):
      """Receive coverage polygons from worker."""
      self._last_coverage_data = coverage_data
      self.map_view.update_coverage(coverage_data)
  ```

- [ ] **Step 3: Run tests**

  ```
  pytest tests/ -v -k "not tier1"
  ```
  Expected: all previously-passing tests still pass.

- [ ] **Step 4: Commit**

  ```
  git add src/gui/main_window.py
  git commit -m "refactor: remove coverage-area and void-pct result labels from status bar"
  ```

---

## Task 3: Remove timing estimate widgets from ControlPanel

**Files:**
- Modify: `src/gui/control_panel.py`

Removes `label_computing`, `label_resolution_estimate`, `_measured_elapsed`, `recalibrate_estimate()`, `_on_resolution_changed()`, and their signal connection.

- [ ] **Step 1: Delete widgets from `_create_widgets`**

  Remove these blocks:
  ```python
  self.label_resolution_estimate = QLabel(f"Est. {RESOLUTION_ESTIMATES['Fast']}")
  self.label_resolution_estimate.setStyleSheet("color: #aaa; font-size: 11px;")

  # Per-preset measured elapsed time (recalibrated after each run)
  self._measured_elapsed = {}
  ```
  and:
  ```python
  # Progress indicator
  self.label_computing = QLabel("Ready")
  self.label_computing.setStyleSheet("color: #4a9eff; font-weight: bold;")
  ```

- [ ] **Step 2: Remove label from `_create_layout`**

  In the resolution group layout, delete:
  ```python
  res_row.addWidget(self.label_resolution_estimate)
  ```
  (The `combo_resolution` widget itself stays — keep `res_row.addWidget(self.combo_resolution)`.)

  Also delete:
  ```python
  layout.addWidget(self.label_computing)
  ```

- [ ] **Step 3: Remove signal connection from `_connect_signals`**

  Delete:
  ```python
  self.combo_resolution.currentIndexChanged.connect(self._on_resolution_changed)
  ```

- [ ] **Step 4: Delete `_on_resolution_changed` and `recalibrate_estimate` methods**

  Remove both complete methods:
  ```python
  def _on_resolution_changed(self, index: int):
      """Update estimate label when resolution preset changes."""
      name = self.combo_resolution.itemData(index)
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
      name = self.combo_resolution.currentData()
      index = self.combo_resolution.currentIndex()
      self._measured_elapsed[name] = elapsed_seconds
      self._on_resolution_changed(index)
  ```

- [ ] **Step 5: Run tests**

  ```
  pytest tests/ -v -k "not tier1"
  ```
  Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

  ```
  git add src/gui/control_panel.py
  git commit -m "refactor: remove timing estimate labels and recalibration machinery from control panel"
  ```

---

## Task 4: Make Ultra the default resolution

**Files:**
- Modify: `src/gui/control_panel.py`

`RESOLUTION_PRESETS` is an ordered dict with keys `Fast=0, Standard=1, High=2, Ultra=3`. Ultra is at index 3.

- [ ] **Step 1: Change combo default index**

  In `_create_widgets`, change:
  ```python
  self.combo_resolution.setCurrentIndex(0)  # Fast by default
  ```
  to:
  ```python
  self.combo_resolution.setCurrentIndex(3)  # Ultra by default
  ```

- [ ] **Step 2: Change `ComputationRequest` dataclass defaults**

  `ComputationRequest` is in `src/gui/control_panel.py`. Change the two default field values:
  ```python
  # Before:
  azimuth_step_deg: float = 2.0    # from resolution preset — NEVER hardcode in worker
  range_step_m: float = 200.0      # from resolution preset — NEVER hardcode in worker
  
  # After:
  azimuth_step_deg: float = 0.5    # Ultra preset default
  range_step_m: float = 50.0       # Ultra preset default
  ```

- [ ] **Step 3: Update test for default resolution**

  In `tests/test_resolution_and_shadow.py`, `test_computation_request_default_resolution` currently asserts Fast values. Change:
  ```python
  def test_computation_request_default_resolution():
      """Default resolution must be Ultra preset (0.5°/50m)."""
      req = ComputationRequest(
          radar_lat=51.0, radar_lon=0.0,
          site_elevation_amsl_m=100.0, antenna_amsl_m=120.0,
          k_factor=1.333, max_range_km=200.0,
          height_bands_m=[50.0],
          diffraction_guard_deg=0.5,
      )
      assert req.azimuth_step_deg == 0.5
      assert req.range_step_m == 50.0
  ```

- [ ] **Step 4: Run tests**

  ```
  pytest tests/test_resolution_and_shadow.py::test_computation_request_default_resolution -v
  pytest tests/test_resolution_and_shadow.py::test_resolution_presets_values -v
  ```
  Expected: both PASS.

- [ ] **Step 5: Commit**

  ```
  git add src/gui/control_panel.py tests/test_resolution_and_shadow.py
  git commit -m "feat: make Ultra resolution the default preset"
  ```

---

## Task 5: Wire DEMPreprocessor.fill_voids behind a removable flag

**Files:**
- Modify: `src/gui/main_window.py`

The current worker does inline `nodata → 0` but never calls `fill_voids`. We add a module-level boolean that gates the call so it can be toggled off without touching the computation logic.

- [ ] **Step 1: Add `VOID_FILL_ENABLED` flag near the top of `main_window.py`**

  Add this constant after the existing module-level colour constants (near line 33):
  ```python
  # Set False to skip DEMPreprocessor tiered void-fill and use inline nodata→0 only.
  # Void fill adds ~2-5s on first load but produces better terrain in data-sparse tiles.
  VOID_FILL_ENABLED = True
  ```

- [ ] **Step 2: Call `fill_voids` in `ComputationWorker.run()` after the inline nodata replacements**

  Current code (lines 83–85 approximately):
  ```python
  if nodata is not None:
      dem_data = np.where(dem_data == nodata, 0.0, dem_data)
  dem_data = np.where((dem_data < -500) | (dem_data > 9000), 0.0, dem_data)
  ```

  Replace with:
  ```python
  if nodata is not None:
      dem_data = np.where(dem_data == nodata, 0.0, dem_data)
  dem_data = np.where((dem_data < -500) | (dem_data > 9000), 0.0, dem_data)

  if VOID_FILL_ENABLED:
      from src.dem_preprocessor import fill_voids
      self.progress_update.emit("Filling DEM voids (first load may take a moment)...")
      try:
          _vf_result = fill_voids(dem_data)
          dem_data = _vf_result["filled"]
          self.progress_update.emit(
              f"Void fill done — {_vf_result['void_fraction']*100:.1f}% of pixels filled"
          )
      except Exception as _vf_err:
          self.progress_update.emit(f"Void fill skipped: {_vf_err}")
  ```

  Key points:
  - `fill_voids` is imported lazily inside the flag block so removing the block leaves no import stubs.
  - Exception is caught with a warn-and-continue so a fill_voids bug never blocks a computation run.
  - The fraction message goes through `progress_update` (status bar label_status) rather than a dedicated label.

- [ ] **Step 3: Run tests**

  ```
  pytest tests/ -v -k "not tier1"
  ```
  Expected: all previously-passing tests still pass. (The worker's DEM path is not exercised by unit tests so this is a smoke-check only.)

- [ ] **Step 4: Commit**

  ```
  git add src/gui/main_window.py
  git commit -m "feat: wire DEMPreprocessor.fill_voids behind VOID_FILL_ENABLED flag in worker"
  ```

---

## Task 6: Fix 3 stale tests in test_resolution_and_shadow.py

**Files:**
- Modify: `tests/test_resolution_and_shadow.py`

The three stale tests were written against old behaviour. The current `_build_shadow_features` returns a **4-corner wedge polygon** (not a 2-point polyline), and `_generate_leaflet_html` renders shadows with `L.polygon(` (not `L.polyline(`). The 0.85× significance filter no longer exists — the only skip condition is `inner_r >= max_range_m`.

### Stale test 1: `test_shadow_rendered_as_polyline_not_polygon` (line 191)

Current code at `map_view.py:332` renders: `L.polygon({slatlngs_json}, ...`. Old test expects `L.polyline`. Fix:

- [ ] **Step 1: Update `test_shadow_rendered_as_polyline_not_polygon`**

  Replace the entire function with:
  ```python
  def test_shadow_rendered_as_polyline_not_polygon():
      """Shadow wedge features must be rendered as L.polygon (4-corner filled wedge)."""
      mv = _make_map_view()
      mv._coverage_opacity_factor = 1.0
      mv._shadow_opacity = 0.5
      mv._max_range_m = 0.0
      mv._shadow_mode = "Wedge"
      mv._shadow_geojson = None

      shadow_feats = [{"latlngs": [[51.6, 0.1], [51.7, 0.2], [51.7, 0.3], [51.6, 0.3]]}]
      html = mv._generate_leaflet_html(51.5, 0.0, [], shadow_feats)

      assert "L.polygon" in html
  ```

  Note: the test name is deliberately kept the same so git blame tracks the change clearly. The docstring explains what the test now actually checks.

### Stale test 2: `test_build_shadow_features_fully_blocked_returns_features` (line 136)

Current `_build_shadow_features` produces a **4-corner** wedge list for each blocked azimuth, not a 2-point centerline. Fix the `len(f["latlngs"]) == 2` assertion:

- [ ] **Step 2: Update `test_build_shadow_features_fully_blocked_returns_features`**

  Change:
  ```python
  assert len(f["latlngs"]) == 2   # inner point + outer point
  ```
  to:
  ```python
  assert len(f["latlngs"]) == 4   # 4-corner wedge polygon
  ```

### Stale test 3: `test_build_shadow_features_significance_filter` (line 175)

Old code filtered out azimuths where `inner_r >= 0.85 * max_range_m`. Current code only skips when `inner_r >= max_range_m`. With `ranges_m = [90_000] * 90 + [50_000] * 90` and `max_r = 100_000`, neither batch is skipped → 180 features produced.

- [ ] **Step 3: Update `test_build_shadow_features_significance_filter`**

  Replace the whole function with:
  ```python
  def test_build_shadow_features_significance_filter():
      """Only azimuths that reach max_range_m exactly are skipped (no 0.85x threshold)."""
      mv = _make_map_view()
      max_r = 100_000.0
      # 90 azimuths at 90 000 m, 90 at 50 000 m — both below max_r → all 180 kept
      ranges_m = [90_000.0] * 90 + [50_000.0] * 90
      feats = mv._build_shadow_features(
          ant_lat=51.5, ant_lon=0.0,
          ranges_m=ranges_m,
          azimuth_step_deg=2.0,
          max_range_m=max_r,
      )
      assert len(feats) == 180   # current code only skips inner_r >= max_range_m
  ```

- [ ] **Step 4: Run the three repaired tests**

  ```
  pytest tests/test_resolution_and_shadow.py::test_shadow_rendered_as_polyline_not_polygon \
         tests/test_resolution_and_shadow.py::test_build_shadow_features_fully_blocked_returns_features \
         tests/test_resolution_and_shadow.py::test_build_shadow_features_significance_filter -v
  ```
  Expected: all three PASS.

- [ ] **Step 5: Run full test suite**

  ```
  pytest tests/ -v -k "not tier1"
  ```
  Expected: 0 failures (the 3 previously-stale tests now pass; all others unchanged).

- [ ] **Step 6: Commit**

  ```
  git add tests/test_resolution_and_shadow.py
  git commit -m "fix: update 3 stale shadow tests to match current 4-corner wedge polygon behaviour"
  ```

---

## Self-Review Checklist

| Requirement | Task covering it |
|-------------|-----------------|
| Remove computation result section — frontend (label_computing, label_resolution_estimate, recalibrate) | Task 3 |
| Remove computation result section — backend (computation_timed signal, _elapsed, _on_computation_timed) | Task 1 |
| Remove status-bar result labels (coverage area, void pct) | Task 2 |
| Ultra resolution as default (combo + dataclass defaults) | Task 4 |
| Update default-resolution test | Task 4, Step 3 |
| Wire fill_voids with removable flag | Task 5 |
| Fix test_shadow_rendered_as_polyline_not_polygon | Task 6, Step 1 |
| Fix test_build_shadow_features_fully_blocked_returns_features | Task 6, Step 2 |
| Fix test_build_shadow_features_significance_filter | Task 6, Step 3 |
