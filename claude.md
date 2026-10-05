# CLAUDE.md — Radar Coverage Analysis Tool

**Developer:** Saanann Roy | B.Tech CSE Sem 4
**References:** BEL paper (P.K. Gupta, V.K. Gupta) + Cambridge Pixel SPx Radar Coverage Tool

---

## PROJECT STATE (June 2026)

**Status: Fully implemented.** All 6 backend engines wired into GUI. End-to-end: load SRTM GeoTIFF → compute terrain-masked coverage → coloured polygons on Leaflet map + polar OVD diagram. Top-K Site Finder implemented with capped-radial bbox scoring, per-candidate DEM elevation, and target height range inputs.

**Completed features:** Shadow layer (Wedge + Polygon modes), beam elevation angle controls (min/max beam, donut polygon rendering), GUI bug fixes (scroll panel, spinbox styling, polar OVD min-height, shadow opacity sync, error-path button reset), DEMPreprocessor.fill_voids wired behind `VOID_FILL_ENABLED` flag in worker, computation resolution hardcoded to Ultra (0.5°/50m — no combo box), Top-K Site Finder with grid search, DEM-cached scoring, capped-radial bbox scoring, per-candidate terrain elevation lookup, target height range spinboxes (Min/Max AGL), live candidate count estimate, elevation propagated to Load button so display computation uses correct antenna height. **Custom height bands**: user can add/remove bands, edit height (m), and pick band colour via QColorDialog — defaults are Cambridge Pixel colours. Score = unique km² at MIN target height (most conservative, most discriminating). Result rows show "X km² @ N m AGL" + ℹ info button explains scoring. Control panel scroll area auto-adjusts with horizontal scrollbar fallback.

### File map

```
src/
  earth_model.py         ← K-factor, R_eff, curvature, horizon
  dem_preprocessor.py    ← SRTM void detection + tiered fill
  dem_manager.py         ← SRTM tile discovery + mosaic (NOT wired in worker — see Known Gaps)
  terrain_engine.py      ← DEM load, void preprocess, radial extract
  obstruction_engine.py  ← CSV obstacle load + inject
  visibility_engine.py   ← horizon tracking, AGL multi-height
  coverage_engine.py     ← polar→GeoJSON, area, export
  shadow_builder.py      ← run-length blocked segment extraction + shapely polygon merging
  site_optimizer.py      ← generate_grid_points() + _ray_bbox_distances() + compute_coverage_score() — no Qt
  gui/
    main_window.py       ← QMainWindow + ComputationWorker + OptimizationWorker wiring
    control_panel.py     ← all input widgets + ComputationRequest dataclass + Top-K panel
    map_view.py          ← QWebEngineView + hand-written Leaflet 1.9.4 HTML + bbox/top-K markers
    polar_view.py        ← Matplotlib embedded polar OVD diagram
    optimization_worker.py ← OptimizationWorker(QThread) — grid-search top-K site ranking
tests/
  test_beam_angles.py          ← 7 tests for beam gate math + dict format (all pass)
  test_tier1_validation.py     ← V1–V5 synthetic tests (pre-existing import issue)
  test_resolution_and_shadow.py← 13 tests; all pass
  test_shadow_builder.py       ← 8 tests (all pass)
  test_dem_manager.py          ← 8 tests (all pass)
  test_site_optimizer.py       ← 7 tests: grid generation, _ray_bbox_distances, capped-radial scoring (all pass)
  conftest.py                  ← PyQt6 headless stub for CI
main.py                ← entry point: python main.py
```

### Bugs fixed (do not reintroduce)

| Bug | Fix |
|-----|-----|
| Coverage always same circle | Per-range AGL: `arctan2(h_apparent[j] + h_agl − antenna_amsl, range[j])` |
| Profile range started at 200m | `ranges = np.arange(0.0, max_range + step, step)` — starts at 0 |
| Out-of-DEM points used boundary pixel | `in_bounds` mask; out-of-DEM → `0.0` (sea level) |
| Leaflet CDN blocked (`L is not defined`) | `LocalContentCanAccessRemoteUrls=True` in QWebEngineSettings |
| Coverage polygons never rendered | Replaced Folium with hand-written `L.polygon()` HTML |
| `polygon_area` crash (pyproj 3.x) | `geod.geometry_area_perimeter(shapely.Polygon)` |
| 100 m AGL hard-capped at ~11.5 km | Apply diffraction guard only when `h_apparent > 0` |
| Coverage ends at near-range shadow | Use last-visible bin: `ranges[1:][visible_idx[-1]]` |
| Shadow zones shown as thin lines | `L.polygon()` 4-corner wedge, `fillColor:#8b0000`, `weight:0` |
| COMPUTE button stuck after error | `_on_computation_error` now calls `set_computing_finished()` |
| Control panel clips on short screens | `_create_layout` wraps content in `QScrollArea` (no horizontal bar) |
| Height-bands nested scroll / cramped | Replaced `QTableWidget` with inline `QHBoxLayout` checkbox rows per band |
| QSpinBox arrows invisible in dark theme | Added `::up-button`/`::down-button` sub-control styles + hover to `dark_stylesheet` |
| Polar OVD title clipped when map maximised | `tight_layout(pad=0.5)`, title `pad` 20→8, `setMinimumHeight(200)`, splitter `setCollapsible(1,False)` |
| Shadow opacity renders at 50% (slider shows 55%) | `MapView._shadow_opacity` default 0.5→0.55 |
| Computation result clutter in status bar | Removed `label_coverage_area`, `label_void_pct`, timing signal `computation_timed` |
| Resolution combo cluttering control panel | Removed combo + presets; hardcoded Ultra (0.5°/50m) in `ComputationRequest` defaults |
| SRTM voids not filled in worker | `VOID_FILL_ENABLED = True` in `main_window.py` gates `fill_voids()` call after DEM load |
| Control panel left side cut off | `setMinimumWidth(255)`, `setMaximumWidth(400)`, `setSizes([270,1200])` on main splitter; DEM/Obstructions Browse buttons stacked vertically below QLineEdit |
| Top-K scores inflated by DEM boundary (ocean) | `compute_coverage_score` clips coverage polygon to bbox before computing area; only in-bbox coverage counts toward score |
| `nodata` NameError when pre-loaded DEM passed | `nodata = None` initialised before the `if dem_data is None` guard in `compute_coverage_score` |
| `_update_site_estimate` ZeroDivisionError at poles | `max(0.01, math.cos(lat_c))` clamp in longitude step calculation |
| Top-K always picks edge/coastal site regardless of target height | Scoring used raw polygon area (quadratic in range) — replaced with capped-radial: `effective_range(θ) = min(C(θ), D(θ))` where D(θ) = distance to bbox edge. Via `_ray_bbox_distances()` in `site_optimizer.py`. Rewards sites covering all bbox edges equally. |
| All candidates evaluated at same fixed elevation (control-panel value) | `OptimizationWorker` now samples DEM at each `(lat, lon)` with nearest-pixel lookup; sets `site_elevation_amsl_m = candidate_elev` and `antenna_amsl_m = candidate_elev + mast_agl` per candidate. Highland peaks now score correctly. |
| Load button uses wrong elevation → display coverage wrong (all red / too large) | Optimizer emits `(lat, lon, score, elev_amsl_m)` 4-tuples. `load_site_requested` signal carries elevation. `set_radar_position()` updates `spin_site_elev`. Display compute uses correct antenna height. |
| Top-K scores double-counted (×N bands = inflated) | Scoring changed to min-height-band only: `score_h = min(heights_agl)`; single polygon area. No double-counting; operationally meaningful (low altitude = binding constraint). |
| Score label had no context — user couldn't tell what km² meant | Score rows now show `"{score:.0f} km²  @  {h} m AGL"`; ℹ button beside Find Top-K opens `QMessageBox` explaining metric. |
| Control panel content clipped when splitter dragged narrow | `ScrollBarAsNeeded` on both axes + `inner.setMinimumWidth(295)` — horizontal scrollbar appears instead of clipping. |
| Control panel too narrow at launch, bbox/height rows clipping right-side spinboxes | Min 255→310, max 400→520, initial 270→360. Bbox spinboxes use `setPrefix("Min "/"Max ")`, 2 per row instead of 4 widgets per row. Height range row same. |
| Height bands static — user couldn't change heights or colours | Dynamic band rows: checkbox + QSpinBox (height) + QPushButton (colour swatch, opens QColorDialog) + remove `−` button. `+ Add Band` adds a row. `height_bands_config_changed` signal propagates to `MapView.set_height_band_config()` and `PolarView.set_height_band_config()`. Initial sync on startup via `_emit_band_config_changed()` call in `main_window._connect_signals()`. |

---

## HOW THE COMPUTATION WORKS

### `ComputationWorker.run()` — `src/gui/main_window.py`

1. Open DEM via rasterio → float64, nodata→0, out-of-range→0
2. Build vectorised `sample_dem(lats, lons)` closure from affine transform
3. Create `EarthModel(k_factor)` and `VisibilityEngine(earth, diffraction_guard_rad)`
4. Precompute: `min_beam_rad`, `max_beam_rad` from request
5. For each azimuth in `np.arange(0, 360, AZIMUTH_STEP)`:
   - `GEOD.fwd(...)` → profile lats/lons; fix bin 0 to antenna; sample DEM; inject obstructions
   - `vis.compute_horizon_angles(profile, antenna_amsl_m)` → `horizon_angles`, `h_apparent`
   - For each `h_agl`:
     ```python
     target_angles   = np.arctan2(h_apparent + h_agl - antenna_amsl_m, ranges)
     terrain_visible = target_angles[1:] >= horizon_angles[1:]          # Gate 1: terrain
     beam_within     = (target_angles[1:] >= min_beam_rad) & \
                       (target_angles[1:] <= max_beam_rad)               # Gate 2: beam window
     combined_mask   = terrain_visible & beam_within
     visible_idx     = np.where(combined_mask)[0]
     max_r = ranges[1:][visible_idx[-1]] if visible_idx.size else ranges[1]  # outer boundary
     min_r = ranges[1:][visible_idx[0]]  if visible_idx.size else ranges[1]  # inner boundary
     coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)
     inner_ranges_m[h_agl][i]    = min_r
     # Shadow uses terrain_visible ONLY — beam exclusion ≠ terrain blocking
     if h_agl == _min_h_agl:
         segs = extract_blocked_segments(terrain_visible, ranges, az)
     ```
6. Emit `coverage_computed`, `polar_data_ready`, `shadow_data_ready`

**`coverage_data` format** (emitted via `coverage_computed` signal):
```python
{height_m: {"outer": [(lat, lon), ...], "inner": [(lat, lon), ...] or None}}
# inner is None when min_beam_deg <= -89.9° (default ±90°) or inner radius degenerate
```

### `ComputationRequest` dataclass — `src/gui/control_panel.py`

```python
@dataclass
class ComputationRequest:
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
    azimuth_step_deg: float = 0.5    # hardcoded Ultra — no combo box in GUI
    range_step_m: float = 50.0       # hardcoded Ultra — no combo box in GUI
    min_beam_deg: float = -90.0      # beam elevation window — ±90° = no constraint
    max_beam_deg: float = 90.0
```

Resolution is fixed at Ultra (0.5°/50m). There is no combo box. Do not add a resolution selector back — if a different resolution is ever needed, change the dataclass defaults.

### Top-K Site Finder — `src/site_optimizer.py` + `src/gui/optimization_worker.py`

**`generate_grid_points(min_lat, min_lon, max_lat, max_lon, step_m)`**
Steps geodesically (GEOD.fwd, lon-first) across the bbox at `step_m` spacing. Returns `[(lat, lon), ...]`.

**`compute_coverage_score(request, *, dem_data=None, dem_transform=None, bbox=None) → float`**
- Mirrors `ComputationWorker.run()` radial math exactly — same curvature, horizon, two-gate visibility
- `dem_data` / `dem_transform`: pass pre-loaded arrays to skip `rasterio.open()` per call (critical for performance)
- `bbox=(min_lat, min_lon, max_lat, max_lon)`: each azimuth's coverage range is capped at the distance to the bbox edge (`_ray_bbox_distances()`). **Always pass bbox** — without it, edge sites near ocean score artificially high.
- **Score = km² of bbox covered at `min(heights_agl)` only.** The minimum height is the binding constraint: a site that covers region X at 100 m AGL also covers it at 3000 m AGL (higher targets clear terrain more easily). Using the max-height polygon or a union would collapse all high-elevation sites to the same score (3000 m AGL covers the bbox from anywhere). Using only min height gives maximum discriminating power and operational relevance.
- Void fill intentionally skipped (inline nodata→0 only)

**`OptimizationWorker(QThread)`** — `src/gui/optimization_worker.py`
```
Scout resolution: SCOUT_AZIMUTH_STEP=5.0°, SCOUT_RANGE_STEP=500.0m (~100× faster than Ultra)

run():
  1. Load DEM ONCE before the loop (rasterio.open → float64, nodata→0, clamp)
  2. generate_grid_points(*bbox, grid_step_m) → candidate list
  3. For each (lat, lon): compute_coverage_score(req, dem_data=..., dem_transform=..., bbox=bbox)
  4. Sort by score desc → emit top-K
```

Signals: `site_scored(i, total)`, `optimization_complete([(lat,lon,score_km2),...])`, `optimization_error(str)`, `progress_update(str)`.

**DEM boundary warning:** Points outside the loaded DEM tile get elevation=0 (sea level). If max_range_km extends beyond the DEM tile, coverage scores are inflated in those directions. Always use a DEM that covers at least `max_range_km` in every direction from the search bbox, or reduce max_range_km to match DEM extent.

---

## CORE PHYSICS

**Effective Earth Radius:** `R_eff = K * 6_371_000` — K=4/3 standard. NEVER hardcode.

**Curvature correction** (apply to terrain, not target):
`delta_h = d² / (2 * R_eff)` → `h_apparent = h_terrain_AMSL - delta_h`
At 50 km, K=4/3: ≈94 m. Never skip.

**Radar horizon:** `R_horizon(h) = sqrt(2 * R_eff * h)` → `R_total = R_horizon(h_radar) + R_horizon(h_target)`

**Horizon tracking:**
```
obs_angle[i] = atan2(h_apparent[i] - h_antenna_AMSL, range[i])
if h_apparent[i] > 0: obs_angle[i] += diffraction_guard_rad   # guard on terrain only
horizon[i] = max(horizon[i-1], obs_angle[i])
```
Point visible if `target_angle[i] >= horizon[i]`.

**Multi-height AGL (critical — previously broken):**
`target_angle[j] = arctan2(h_apparent[j] + h_agl - antenna_amsl, ranges[j])`
**NEVER** use `h_amsl = site_elev + h_agl` as a fixed constant — eliminates terrain blocking.

**Geodesy:** `pyproj.Geod(ellps='WGS84')` everywhere. Never flat Earth (>1% error beyond 10 km).

---

## SRTM VOID PREPROCESSING

SRTM NoData = -32768. If unfilled: `h_apparent` → large negative → silent wrong results.

Pipeline (cache as `*_filled.tif`): detect voids → Tier 1 small (<10 px, `generic_filter`) → Tier 2 medium (10–100 px, `griddata linear`) → Tier 3 large (>100 px, `griddata nearest`) → `nan_to_num(0)` fallback → `gaussian_filter` boundary smoothing → verify 0 remaining voids → save float32 GeoTIFF.

**Worker void-fill:** After inline nodata→0 cleanup, worker calls `DEMPreprocessor.fill_voids()` when `VOID_FILL_ENABLED = True` (module-level flag in `main_window.py`). Set `False` to bypass (inline nodata→0 only). Progress is reported via `progress_update` signal. Exception in fill_voids is caught and logged as a warning — computation continues regardless.

---

## KEY IMPLEMENTATION RULES

**Computation:**
- float64 everywhere; numpy-vectorised inside radial loop (no Python loops over bins)
- `np.arctan2(y, x)` always; K-factor from `earth_model.k` always
- Profile ranges start at 0; `elevations[0] = site_elev_m`
- Out-of-DEM → `0.0` (sea level), NOT boundary pixel clipping
- Visibility check skips bin 0: `target_angles[1:] >= horizon_angles[1:]`
- `azimuth_step_deg` / `range_step_m` — read from `ComputationRequest`; hardcoded to Ultra (0.5°/50m) via dataclass defaults; change defaults there, never in the worker

**Beam angles:**
- Two-gate check: `combined_mask = terrain_visible & beam_within`
- Shadow extraction uses `terrain_visible` ONLY — beam exclusion is not terrain blocking
- Inner ring only emitted when `req.min_beam_deg > -89.9` AND `max(inner_r_arr) > RANGE_STEP_M`
- `coverage_data[h]["outer"]` = outer boundary; `["inner"]` = donut hole or `None`

**Top-K scoring:**
- ALWAYS pass `bbox` to `compute_coverage_score()` — omitting it causes edge-of-bbox bias
- ALWAYS pass pre-loaded `dem_data`/`dem_transform` from `OptimizationWorker` — never let scorer open the file per call
- Scout resolution (5°/500m) is intentional — do not change to Ultra; full Ultra runs after user clicks Load
- `nodata = None` must be initialised before the `if dem_data is None` guard — prevents NameError on pre-loaded path
- Score = **min-height band only** (`score_h = min(heights_agl)`). Do NOT revert to sum-of-bands (inflates scores ×N) or unary_union (dominated by max-height polygon, all high-elevation sites score identically)
- `_last_score_height_m` is stored on the control panel when Find is clicked; `show_optimization_results` reads it to label each row `"X km²  @  N m AGL"`

**Map rendering:**
- `LocalContentCanAccessRemoteUrls=True` in `QWebEngineSettings`
- Hand-write Leaflet HTML with `L.polygon()` — do NOT use Folium GeoJson
- Shadow layer rendered before coverage polygons (shadow underneath)
- `_build_coverage_features` returns `latlngs` as list-of-rings: `[outer_ring]` or `[outer_ring, inner_ring]`
- `L.polygon(latlngs)` natively renders donut when two rings supplied — no JS changes needed
- Shadow wedge: 4-corner `L.polygon()`, `fillColor:'#8b0000'`, `weight:0`, lowest height band only
- Shadow mode toggle: Wedge (per-azimuth `L.polygon`) vs Polygon (`L.geoJSON` with `unary_union`)
- `GEOD.fwd(lons, lats, azimuths, ranges)` — lons first (pyproj convention)
- GeoJSON polygon: first coord == last coord (closed ring)
- Polar diagram: `theta_zero='N'`, `theta_direction=-1`

**Control panel:**
- `setMinimumWidth(310)` / `setMaximumWidth(520)` on `control_panel` in `main_window.py`
- `main_splitter.setSizes([360, 1200])` — sets initial panel width on launch
- DEM File and Obstructions groups use stacked `QVBoxLayout` (line edit above button) — never side-by-side `QHBoxLayout`, which clips the button on narrow panels
- Bbox rows: 2 spinboxes per row, each with `setPrefix("Min "/"Max ")` — do NOT use 4-widget rows (QLabel + spin + QLabel + spin) which overflow the panel
- Height range row: same — 2 spinboxes with prefix, no separate label widgets
- Scroll area: `ScrollBarAsNeeded` (both axes) + `inner.setMinimumWidth(295)` — content is never clipped; horizontal scrollbar appears as fallback when panel dragged very narrow

**Height band system:**
- `DEFAULT_HEIGHT_BANDS` list in `control_panel.py` defines defaults (Cambridge Pixel colours/opacities)
- `ControlPanel._band_rows` = list of dicts: `{color, opacity, checkbox, spin, btn_color, widget}`
- `_add_band_row(height_m, color, opacity, emit=False)` — adds one row widget to `_bands_container`
- `_remove_band_row(row_info)` — removes widget, min 1 band enforced
- `_pick_band_color(row_info, btn)` — opens `QColorDialog`, updates `row_info["color"]` and button style
- `height_bands_config_changed = pyqtSignal(list)` — fires on any band change; carries `[{height_m, color, opacity, enabled}, ...]`
- `MapView.set_height_band_config(bands)` — rebuilds `_band_config` dict used by `_get_color_info()` and legend
- `PolarView.set_height_band_config(bands)` — rebuilds `_band_colors` dict used in `update_data()`
- Initial sync: `main_window._connect_signals()` calls `control_panel._emit_band_config_changed()` after wiring the signal

---

## TECH STACK

```bash
conda install -c conda-forge rasterio gdal pyproj geopandas scipy numpy pandas shapely
pip install PyQt6 PyQt6-WebEngine matplotlib
# folium NOT required
```

GUI: PyQt6 + Matplotlib (polar) + Leaflet 1.9.4 HTML via QWebEngineView. Dark theme `#1e1e2e`.
DEM: SRTM3 GeoTIFF primary; DTED Level 1/2 same code path.
Leaflet CDN: `https://unpkg.com/leaflet@1.9.4/dist/leaflet.{css,js}`.

## HEIGHT BAND COLOURS

Colours are **user-configurable at runtime** via the dynamic band rows in the control panel. The hardcoded fallback tables in `map_view.py` (`HEIGHT_BAND_COLORS`) and `polar_view.py` (`HEIGHT_BAND_COLORS`) are used only as the initial default before the first `height_bands_config_changed` signal fires. After that, `MapView._band_config` and `PolarView._band_colors` are live.

Default colours (Cambridge Pixel convention):
```python
DEFAULT_HEIGHT_BANDS = [
    {"height_m": 50,   "color": "#00cc44", "opacity": 0.45},
    {"height_m": 100,  "color": "#aacc00", "opacity": 0.38},
    {"height_m": 500,  "color": "#ff8800", "opacity": 0.32},
    {"height_m": 1000, "color": "#ff3300", "opacity": 0.22},
    {"height_m": 3000, "color": "#cc00ff", "opacity": 0.15},
]
```
Draw highest height first, lowest last (lowest band painted on top). Opacity is stored per band; new user-added bands default to opacity 0.30.

---

## VALIDATION

### Tier 1 — No DEM (`tests/test_tier1_validation.py`)
| ID | Test | Pass condition |
|----|------|----------------|
| V1 | Flat terrain | Circle ±1% of `R_total(h_antenna, H)` |
| V2 | K-factor ratio | `range(K=4/3) / range(K=1.0) = sqrt(4/3) ± 0.1%` |
| V3 | Single obstacle az=90° | az=90° blocked; az=0°,180°,270° unaffected |
| V4 | Diffraction guard 0° vs 0.5° | `range(0.5°) ≤ range(0°)` |
| V5 | Synthetic void fill | `fill_successful=True`, max deviation < 50 m |

### Tier 2 — Real DEM (tiles from srtm.csi.cgiar.org → `data/dem/`)
| ID | Site | Tile | What it proves |
|----|------|------|----------------|
| V7 | Dover 51.13°N 1.32°E | N51E001 | East/West ratio >1.5 |
| V8 | Heraklion 35.33°N 25.13°E | N35E025 | North/South ratio >2.0 |
| V9 | Ben Nevis 56.8°N −5.0°E | N56W006 | Irregular Highland coverage |
| V10 | Al Jouf 29.78°N 40.10°E | N29E040 | K-factor ratio on flat desert |
| V11 | Canvey Island 51.52°N 0.58°E | N51E000 | Obstruction injection masks Shard |

---

## KNOWN GAPS

### Phase B — remaining Cambridge Pixel feature parity
- Start range / inner donut hole via separate spinbox (beam angles give inner boundary; dedicated start-range control not yet built)
- Per-band opacity control (real-time slider per height) — colour is now custom but opacity is internal only
- Map brightness/contrast sliders
- DMS coordinate input
- Azimuth extent (partial sector, e.g. 90°–270°)
- Above Sea Level vs Ground toggle for target heights
- True offline map (embed Leaflet + OSM tiles locally)

### Existing gaps

1. **GeoJSON export is placeholder.** `_on_export_geojson()` uses placeholder ranges instead of `CoverageEngine.polar_to_geojson()`. Rewrite to build from `coverage_ranges_m` during computation. Also: export iterates `band_data["outer"]` now (updated for new format).

2. **True offline not achieved.** Leaflet + map tiles load from CDN.

3. **`dem_manager.py` not wired.** `DEMManager.open_mosaic()` reverted after multi-tile mosaics produced boxy wrong-shape coverage (nodata in mosaic treated as terrain void). Fix: fill voids per-tile before merging.

4. **`test_tier1_validation.py` pre-existing import error.** `from visibility_engine import VisibilityEngine` fails due to relative import; unrelated to current work.

5. **`PyQt6.QtWebChannel` DLL unavailable on this machine.** `_HAS_WEBCHANNEL = False` at runtime — Leaflet.draw bbox and top-K marker click events cannot reach Python via JS bridge. The bbox must be set via spinboxes; Load buttons in the sidebar work normally. Fix: install a compatible `PyQt6-Qt6` build that includes `Qt6WebChannel.dll`.

---

## WHAT NOT TO BUILD
Radar equation/RCS, ITU-R P.526 diffraction, propagation loss, multi-site comparison, DEM download, cloud/web, 3D, animation, UTM reprojection.

## SAMPLE DATA

`data/obstructions/sample_obstructions.csv`: `lat,lon,height_amsl_m,type,description` / `34.16,77.59,3350,tower,Sample Tower`

`tests/data/london_obstructions.csv`: Shard (310 m, 51.5045°N 0.0196°W) + Canary Wharf (235 m)

## CODE STYLE
- Docstrings: physical meaning + units + formula source (`# BEL paper Eq. 3`)
- Parameter names include units: `range_m`, `height_amsl_m`, `angle_rad`
- No magic numbers — named constants at module level
