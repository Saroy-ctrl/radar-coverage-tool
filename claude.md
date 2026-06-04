# CLAUDE.md — Radar Coverage Analysis Tool

**Developer:** Saanann Roy | B.Tech CSE Sem 4
**References:** BEL paper (P.K. Gupta, V.K. Gupta) + Cambridge Pixel SPx Radar Coverage Tool

---

## PROJECT STATE (June 2026)

**Status: Fully implemented and integrated.** All 6 backend engines are wired into the GUI. The app runs end-to-end: load a SRTM GeoTIFF → compute terrain-masked coverage → display coloured polygons on Leaflet map + polar OVD diagram.

**Phase A resolution upgrade complete (June 2026).** Resolution presets added; terrain-blocking shadow layer implemented on Leaflet map.

### What exists at the root

```
src/
  earth_model.py          ← K-factor, R_eff, curvature, horizon (EarthModel class)
  dem_preprocessor.py     ← SRTM void detection + tiered fill (fill_voids function)
  terrain_engine.py       ← DEM load, void preprocess, radial extract (TerrainEngine)
  obstruction_engine.py   ← CSV obstacle load + inject (ObstructionEngine)
  visibility_engine.py    ← horizon tracking, AGL multi-height (VisibilityEngine)
  coverage_engine.py      ← polar→GeoJSON, area, export (CoverageEngine)
  gui/
    main_window.py        ← QMainWindow + ComputationWorker (real backend wired in)
    control_panel.py      ← all input widgets + ComputationRequest dataclass
    map_view.py           ← QWebEngineView + hand-written Leaflet 1.9.4 HTML
    polar_view.py         ← Matplotlib embedded polar OVD diagram
tests/
  test_tier1_validation.py      ← V1–V5 synthetic tests (no DEM needed)
  test_resolution_and_shadow.py ← 8 tests for resolution presets + shadow wedge geometry
  conftest.py                   ← PyQt6 headless stub for CI/test environments
main.py                   ← entry point: python main.py
requirements.txt
data/                     ← gitignored; SRTM tiles go here
```

### Bugs fixed (critical — do not reintroduce)

| Bug | Root cause | Fix applied |
|-----|-----------|-------------|
| Coverage always same circle | `h_amsl = site_elev + h_agl` fixed → ignores terrain | Per-range AGL: `arctan2(h_apparent[j] + h_agl − antenna_amsl, range[j])` |
| Profile range started at 200m | First non-zero bin gave spurious horizon → locked to circle | `ranges = np.arange(0.0, max_range + step, step)` — starts at 0 |
| Out-of-DEM points used boundary pixel | `np.clip` silently returned edge elevation | `in_bounds` mask; out-of-DEM → `0.0` (sea level) |
| Leaflet CDN blocked (`L is not defined`) | `QWebEngine` sandbox blocks remote URLs from `file://` pages | `LocalContentCanAccessRemoteUrls=True` in QWebEngineSettings |
| Coverage polygons never rendered | Folium GeoJson JS fails silently in WebEngine | Replaced Folium entirely with `L.polygon()` hand-written HTML |
| `polygon_area` crash | `pyproj.Geod` has no `polygon_area()` in pyproj 3.x | Status bar uses `geod.geometry_area_perimeter(shapely.Polygon)` |

---

## HOW THE COMPUTATION WORKS (actual implementation)

### Entry point: `ComputationWorker.run()` in `src/gui/main_window.py`

The worker runs in a `QThread`. High-level flow:

1. Open DEM via rasterio → float64 array, nodata → 0, out-of-range → 0
2. Build vectorised `sample_dem(lats, lons)` closure using the affine transform
3. Create `EarthModel(k_factor)` and `VisibilityEngine(earth, diffraction_guard_rad)`
4. For each azimuth in `np.arange(0, 360, AZIMUTH_STEP=2°)` (180 total):
   a. `GEOD.fwd(ant_lon, ant_lat, az, ranges)` → `(lons_p, lats_p)` for all range bins
   b. Fix bin 0 to exact antenna coords; set `elevations[0] = site_elev_m`
   c. `sample_dem(lats_p, lons_p)` → elevation profile
   d. Inject obstructions (if CSV loaded)
   e. `vis.compute_horizon_angles(profile, antenna_amsl_m)` → `horizon_angles`, `h_apparent`
   f. For each `h_agl` in `height_bands_m`:
      ```python
      target_angles = np.arctan2(h_apparent + h_agl - antenna_amsl_m, ranges)
      visible_mask  = target_angles[1:] >= horizon_angles[1:]   # skip bin 0
      blocked_idx   = np.where(~visible_mask)[0]
      max_r = ranges[1:][blocked_idx[0]] if blocked_idx.size else ranges[-1]
      coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)
      ```
5. Emit `coverage_computed`, `polar_data_ready`, `shadow_data_ready`, `computation_timed`

**Key parameters (now user-controlled via resolution preset):**
- `RANGE_STEP_M = req.range_step_m` (200m Fast → 50m Ultra)
- `AZIMUTH_STEP = req.azimuth_step_deg` (2° Fast → 0.5° Ultra)
- Height bands passed as AGL metres (not AMSL)

**New signals emitted after the azimuth loop:**
- `shadow_data_ready(dict)` — `{"ranges_m": list, "azimuth_step_deg": float, "max_range_m": float}` using `min(height_bands_m)` (lowest band, most restrictive)
- `computation_timed(float)` — wall-clock seconds; received by `ControlPanel.recalibrate_estimate()`

### `ComputationRequest` dataclass (src/gui/control_panel.py)

```python
@dataclass
class ComputationRequest:
    radar_lat: float
    radar_lon: float
    site_elevation_amsl_m: float   # DEM elevation at site (set manually in control panel)
    antenna_amsl_m: float          # site_elev + mast + antenna height
    k_factor: float
    max_range_km: float
    height_bands_m: list[float]    # AGL target heights
    diffraction_guard_deg: float
    dem_path: str = None
    obstructions_path: str = None
    azimuth_step_deg: float = 2.0  # from resolution preset — DO NOT hardcode in worker
    range_step_m: float = 200.0    # from resolution preset — DO NOT hardcode in worker
```

### Resolution presets (src/gui/control_panel.py)

```python
RESOLUTION_PRESETS = {
    "Fast":     (2.0,  200.0),   # ~30s
    "Standard": (1.0,  100.0),   # ~2 min
    "High":     (0.5,  100.0),   # ~4 min
    "Ultra":    (0.5,   50.0),   # ~8 min
}
```
Selected via `QComboBox` in "Computation Resolution" group. Estimate label recalibrates after each run using `recalibrate_estimate(elapsed_seconds)`. Preset name stored as `userData` on each combo item — retrieved via `currentData()`, not index arithmetic.

---

## CORE PHYSICS

### Effective Earth Radius (K-factor — NEVER hardcode)
```
R_eff = K * 6_371_000    # K=4/3 standard, K=1.0 optical, K=user-defined
```

### Curvature Correction (apply to TERRAIN, not target)
```
delta_h = d² / (2 * R_eff)
h_apparent = h_terrain_AMSL - delta_h
```
At 50 km, K=4/3: correction ≈ 94 m. Never skip.

### Radar Horizon + Total Range (BEL paper Eq. 2–3)
```
R_horizon(h) = sqrt(2 * R_eff * h)
R_total = R_horizon(h_radar) + R_horizon(h_target)
```

### Obstruction Angle + Horizon Tracking (core algorithm)
```
obs_angle[i] = atan2(h_apparent[i] - h_antenna_AMSL, range[i])
obs_angle[i] += diffraction_guard_rad    # default 0.5° per BEL paper
horizon[i]   = max(horizon[i-1], obs_angle[i])   # cumulative max
```
Point visible if `target_angle[i] >= horizon[i]`.

### Multi-Height AGL (CORRECT — critical, previously broken)
```python
# h_agl = target height above LOCAL terrain at each range bin
# h_apparent[j] = terrain_elevation[j] - curvature_correction(range[j])
target_angle[j] = arctan2(h_apparent[j] + h_agl - antenna_amsl, ranges[j])
```
**NEVER** use `h_amsl = site_elev + h_agl` as a fixed constant. That eliminates terrain blocking entirely.

### Geodesy — NEVER flat Earth (>1% error beyond 10 km)
`pyproj.Geod(ellps='WGS84')` for all distance/bearing. `GEOD.fwd()` for forward projection.

---

## SRTM VOID PREPROCESSING — MANDATORY

SRTM NoData = -32768. If not filled: silent incorrect results (`h_apparent` → large negative).

**Pipeline (run once per tile, cache as `*_filled.tif`):**
1. **Detect:** void if == nodata OR < -500 OR > 9000. Log void %.
2. **Tier 1** small voids (<10 px): `scipy.ndimage.generic_filter(nanmean, size=7)`
3. **Tier 2** medium (10–100 px): `scipy.interpolate.griddata(method='linear')`
4. **Tier 3** large (>100 px): `scipy.interpolate.griddata(method='nearest')`
5. **Fallback:** `np.nan_to_num(arr, nan=0.0)` + warning
6. **Smooth boundaries:** `gaussian_filter(sigma=1)` blended at fill edges only
7. **Verify:** assert remaining voids == 0, max fill deviation < 500 m
8. **Cache:** save as float32 GeoTIFF (LZW compress, nodata=-9999)

If void% > 20%: warn user. Any NaN reaching the computation loop = fatal bug.

**Note:** The current `ComputationWorker.run()` does basic void masking inline (nodata→0, out-of-range→0) but does **not** call `DEMPreprocessor` with the full tiered pipeline. For production use on real SRTM tiles, wire `TerrainEngine.load_tile()` properly.

---

## KEY IMPLEMENTATION RULES

- **float64 everywhere** in computation. Never float32 for range/elevation.
- **numpy vectorised** inside radial loop. No Python loops over range bins.
- `np.arctan2(y, x)` always — never `arctan(y/x)`.
- K-factor read from `earth_model.k` always — no literals.
- GUI computation in `QThread` always — never block main thread.
- Profile ranges **start at 0** (antenna bin); `elevations[0] = site_elev_m`.
- Out-of-DEM points → `0.0` (sea level), NOT boundary pixel clipping.
- AGL target angle: `arctan2(h_apparent + h_agl − antenna_amsl, range)` — per-range.
- Visibility check skips bin 0: `target_angles[1:] >= horizon_angles[1:]`.
- GeoJSON polygon: first coordinate == last coordinate (closed ring).
- Polar diagram: `theta_zero='N'`, `theta_direction=-1` (compass convention).
- Map: `LocalContentCanAccessRemoteUrls=True` in `QWebEngineSettings`.
- Map: hand-write Leaflet HTML with `L.polygon()` — do NOT use Folium GeoJson layer.
- Map shadow layer: rendered **before** coverage polygons in HTML so coverage paints over it.
- Shadow wedges: `color:'#8b0000'`, `fillOpacity:0.55`, `weight:0` — lowest height band only.
- `_build_shadow_features()`: skip azimuth if `inner_r >= max_range_m` (no shadow needed).
- `GEOD.fwd(lons, lats, azimuths, ranges)` — lons first, then lats (pyproj convention).
- `ComputationRequest.azimuth_step_deg` / `range_step_m` — always read from request; NEVER hardcode in worker.
- Area in status bar: `geod.geometry_area_perimeter(shapely.Polygon)` — NOT `polygon_area()`.
- `CoverageEngine.compute_polygon_area()` still uses `GEOD.polygon_area(lons, lats)` — verify this works on your pyproj version.

---

## TECH STACK

```bash
conda install -c conda-forge rasterio gdal pyproj geopandas scipy numpy pandas shapely
pip install PyQt6 PyQt6-WebEngine matplotlib
# folium NOT required — replaced by hand-written Leaflet HTML
```

**GUI:** PyQt6 + Matplotlib (polar) + hand-written Leaflet 1.9.4 HTML via QWebEngineView. Dark theme `#1e1e2e`.
**DEM:** SRTM3 GeoTIFF primary. DTED Level 1/2 same code path (rasterio handles both).
**Leaflet CDN:** pinned to `https://unpkg.com/leaflet@1.9.4/dist/leaflet.{css,js}`. For true offline, download and embed locally.

---

## HEIGHT BAND COLOURS (Cambridge Pixel convention)

```python
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},   # Green (lowest)
    100:  {"color": "#aacc00", "opacity": 0.40},   # Yellow-green
    500:  {"color": "#ff8800", "opacity": 0.40},   # Orange
    1000: {"color": "#ff3300", "opacity": 0.35},   # Red
    3000: {"color": "#cc00ff", "opacity": 0.30},   # Magenta (highest)
}
```
Draw highest height first (largest area), lowest last (painted on top, most restrictive visible).

---

## VALIDATION CASES

### Tier 1 — No DEM needed (run on every change, `tests/test_tier1_validation.py`)
| ID | Test | Pass condition |
|----|------|----------------|
| V1 | Flat terrain, all zeros | Perfect circle ±1% of `R_total(h_antenna, H)` |
| V2 | K-factor ratio | `range(K=4/3) / range(K=1.0) = sqrt(4/3) ± 0.1%` |
| V3 | Single obstacle at 10 km, az=90° | az=90° blocked <10 km; az=0°,180°,270° unaffected |
| V4 | Diffraction guard 0° vs 0.5° | `range(0.5°) ≤ range(0°)` for blocked azimuth |
| V5 | Synthetic void fill | `fill_successful=True`, max deviation < 50 m |

Run with: `python -m pytest tests/test_tier1_validation.py -v`

### Tier 2 — Real DEM (tiles from srtm.csi.cgiar.org, place in `data/dem/`)
| ID | Site | Tile | What it proves |
|----|------|------|----------------|
| V7 | Dover, England 51.13°N 1.32°E | N51E001 | East/West ratio >1.5 (cliff AMSL) |
| V8 | Heraklion, Crete 35.33°N 25.13°E | N35E025 | North/South ratio >2.0 (island + mountain) |
| V9 | Ben Nevis, UK 56.8°N −5.0°E | N56W006 | Irregular coverage (Highland terrain) |
| V10 | Al Jouf, Saudi Arabia 29.78°N 40.10°E | N29E040 | K-factor ratio holds on real flat desert |
| V11 | Canvey Island, UK 51.52°N 0.58°E | N51E000 | Obstruction injection masks Shard bearing |

---

## KNOWN GAPS / NEXT STEPS

### Phase B — UI feature parity with Cambridge Pixel (not yet started)
- Start range / inner donut hole (ring-shaped coverage instead of filled disc)
- Beam elevation angle controls (min/max elevation angle, not just AGL height bands)
- Coverage transparency slider (per-band opacity control in real time)
- Map brightness/contrast sliders
- DMS coordinate input (degrees/minutes/seconds)
- Azimuth extent — compute partial sector (e.g. 90°–270° only)
- Above Sea Level vs Ground toggle for target heights
- True offline map (download Leaflet 1.9.4 + OSM tiles, embed locally)

### Existing gaps

1. **DEM preprocessing not fully wired in GUI.** `ComputationWorker.run()` does basic nodata→0 substitution inline instead of calling `DEMPreprocessor.fill_voids()`. Before running on data-rich tiles (Tibet, Crete), wire `terrain_engine.load_tile()` into the worker.

2. **GeoJSON export is placeholder.** `_on_export_geojson()` in `main_window.py` reconstructs GeoJSON from stored `coverage_data` dict using placeholder ranges — it does not call the full `CoverageEngine.polar_to_geojson()` pipeline properly. Rewrite to build GeoJSON directly from `coverage_ranges_m` during the computation.

3. **True offline not yet achieved.** Map tiles and Leaflet library load from CDN. For full offline operation, download Leaflet 1.9.4 and OSM tiles and embed locally.

4. ~~180 azimuths at 2° resolution~~ **RESOLVED.** Resolution preset selector added — Fast (2°/200m) through Ultra (0.5°/50m) selectable in the control panel.

5. **Tier 1 test suite uses different API than main_window.** Tests call `VisibilityEngine.compute_target_visibility(profile, h_antenna, h_target_amsl_m)` with absolute AMSL height. The worker uses the per-range AGL path directly. Both are correct but test the engine differently.

---

## WHAT NOT TO BUILD
Radar equation/RCS, full ITU-R P.526 diffraction, propagation loss, multi-site comparison, DEM download, cloud/web, 3D, animation, UTM reprojection.

---

## SAMPLE DATA

`data/obstructions/sample_obstructions.csv`:
```
lat,lon,height_amsl_m,type,description
34.1600,77.5900,3350.0,tower,Sample Tower
```

`tests/data/london_obstructions.csv` (for V11):
```
lat,lon,height_amsl_m,type,description
51.5045,-0.0196,310.0,skyscraper,The Shard
51.5034,-0.0009,235.0,skyscraper,Canary Wharf
```

---

## CODE STYLE
- Docstrings: physical meaning + units + formula source (e.g. `# BEL paper Eq. 3`)
- Parameter names include units: `range_m`, `height_amsl_m`, `angle_rad`
- No magic numbers — named constants at module level
- Log void fill stats and DEM bounds on every DEM load
