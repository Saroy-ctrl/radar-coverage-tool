# CLAUDE.md — Radar Coverage Analysis Tool

**Developer:** Saanann Roy | B.Tech CSE Sem 4
**References:** BEL paper (P.K. Gupta, V.K. Gupta) + Cambridge Pixel SPx Radar Coverage Tool

---

## PROJECT STATE (June 2026)

**Status: Fully implemented.** All 6 backend engines wired into GUI. End-to-end: load SRTM GeoTIFF → compute terrain-masked coverage → coloured polygons on Leaflet map + polar OVD diagram.

**Completed features:** Shadow layer (Wedge + Polygon modes), beam elevation angle controls (min/max beam, donut polygon rendering), GUI bug fixes (scroll panel, height-band rows, spinbox styling, polar OVD min-height, shadow opacity sync, error-path button reset), DEMPreprocessor.fill_voids wired behind `VOID_FILL_ENABLED` flag in worker, computation resolution hardcoded to Ultra (0.5°/50m — no combo box).

### File map

```
src/
  earth_model.py       ← K-factor, R_eff, curvature, horizon
  dem_preprocessor.py  ← SRTM void detection + tiered fill
  dem_manager.py       ← SRTM tile discovery + mosaic (NOT wired in worker — see Known Gaps)
  terrain_engine.py    ← DEM load, void preprocess, radial extract
  obstruction_engine.py← CSV obstacle load + inject
  visibility_engine.py ← horizon tracking, AGL multi-height
  coverage_engine.py   ← polar→GeoJSON, area, export
  shadow_builder.py    ← run-length blocked segment extraction + shapely polygon merging
  gui/
    main_window.py     ← QMainWindow + ComputationWorker
    control_panel.py   ← all input widgets + ComputationRequest dataclass
    map_view.py        ← QWebEngineView + hand-written Leaflet 1.9.4 HTML
    polar_view.py      ← Matplotlib embedded polar OVD diagram
tests/
  test_beam_angles.py          ← 7 tests for beam gate math + dict format (all pass)
  test_tier1_validation.py     ← V1–V5 synthetic tests (pre-existing import issue)
  test_resolution_and_shadow.py← 13 tests; all pass
  test_shadow_builder.py       ← 8 tests (all pass)
  test_dem_manager.py          ← 8 tests (all pass)
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

## HEIGHT BAND COLOURS (Cambridge Pixel convention)

```python
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},
    100:  {"color": "#aacc00", "opacity": 0.38},
    500:  {"color": "#ff8800", "opacity": 0.32},
    1000: {"color": "#ff3300", "opacity": 0.22},
    3000: {"color": "#cc00ff", "opacity": 0.15},
}
```
Draw highest height first, lowest last (lowest band painted on top).

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
- Per-band opacity control (real-time slider per height)
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
