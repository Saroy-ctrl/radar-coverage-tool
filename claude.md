# CLAUDE.md — Radar Coverage Analysis Tool

**Developer:** Saanann Roy | B.Tech CSE Sem 4
**References:** BEL paper (P.K. Gupta, V.K. Gupta) + Cambridge Pixel SPx Radar Coverage Tool

---

## CURRENT STATUS (June 2026)

**Backend integration: COMPLETE** — real physics computation (DEM, terrain, visibility) is wired into `ComputationWorker.run()`.

**Key bugs fixed (June 2026 session):**
| Bug | Root Cause | Fix |
|-----|-----------|-----|
| Coverage always same circle | `h_amsl = site_elev + h_agl` — fixed target height means no terrain can block it at that altitude | Changed to per-range AGL: `target_angle = arctan2(h_apparent + h_agl − antenna_amsl, range)` |
| Profile range started at 200m not 0 | First bin at 200m gave a spurious large horizon angle → accumulated max locked coverage to a circle | Profile now starts at `range=0` (antenna bin) with `elev=site_elev_m` |
| Out-of-DEM samples used boundary pixels | `np.clip` returned edge elevation for out-of-bounds, giving uniform false terrain | Added `in_bounds` mask; out-of-DEM → `0.0` (sea level) |
| `L is not defined` JS error | `QWebEngineView` blocks remote CDN URLs from local `file://` pages | Added `LocalContentCanAccessRemoteUrls=True` to WebEngine settings |
| Coverage polygons never rendered | Folium's GeoJson JS failed silently in the WebEngine sandbox | Replaced Folium entirely with hand-written `L.polygon()` Leaflet HTML |
| `polygon_area / polygon_area_perimeter` crash | Neither method exists on `pyproj.Geod` in pyproj 3.x | Replaced with `geod.geometry_area_perimeter(shapely.Polygon)` |

---

## WHAT TO BUILD
Offline desktop app: load SRTM/DTED terrain → compute terrain-masked radar coverage for multiple flight heights → display coloured coverage overlay on map + polar OVD diagram → export PNG/GeoTIFF/GeoJSON. Zero internet during operation (tiles currently from OSM CDN; embed Leaflet locally for full offline).

---

## CORE PHYSICS

### Effective Earth Radius (K-factor — NEVER hardcode)
```
R_eff = K * 6_371_000    # K=4/3 standard, K=1.0 optical, K=user-defined
```

### Curvature Correction (apply to TERRAIN, not target)
```
delta_h = d² / (2 * R_eff)          # d = ground range metres
h_apparent = h_terrain_AMSL - delta_h
```
At 50km, K=4/3: correction ≈ 94m. Never skip this.

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
Point visible if target_angle ≥ horizon_angle at that range.

### Multi-Height AGL (CORRECT IMPLEMENTATION — critical)
```
# h_agl = target height above LOCAL terrain (not site elevation)
# h_apparent[j] = terrain_elevation[j] - curvature_correction(range[j])
target_angle[j] = atan2(h_apparent[j] + h_agl - antenna_amsl, range[j])
visible[j]      = target_angle[j] >= horizon_angle[j]
blocked         = first index where visible == False
max_range       = min(ranges[blocked], max_instrumented_range)
```

**⚠️ NEVER use `h_amsl = site_elev + h_agl` as a fixed number.** That puts targets above the highest mountain → coverage is always a perfect circle. The correct AGL calculation uses per-range terrain heights.

### Geodesy — NEVER flat Earth (>1% error beyond 10km)
Use `pyproj.Geod(ellps='WGS84')` for all distance/bearing.

---

## SRTM VOID PREPROCESSING — MANDATORY

SRTM NoData = -32768. If not filled: `h_apparent = -32768 - curvature` → silent wrong results.

**Pipeline (run once per tile, cache as `*_filled.tif`):**
1. **Detect:** void if value == nodata OR < -500 OR > 9000. Log void %.
2. **Tier 1** small voids (<10px): `scipy.ndimage.generic_filter(nanmean, size=7)`
3. **Tier 2** medium (10–100px): `scipy.interpolate.griddata(method='linear')`
4. **Tier 3** large (>100px): `scipy.interpolate.griddata(method='nearest')`
5. **Fallback:** `np.nan_to_num(arr, nan=0.0)` + warning
6. **Smooth boundaries:** `gaussian_filter(sigma=1)` blended at fill edges only
7. **Verify:** assert remaining voids == 0, max fill deviation < 500m
8. **Cache:** save as float32 GeoTIFF (`lzw` compress, nodata=-9999)

If void% > 20%: warn user. Any NaN reaching visibility_engine = fatal bug.

---

## ARCHITECTURE

```
src/
  earth_model.py        ← K-factor, R_eff, curvature, horizon range
  terrain_engine.py     ← DEM load, tile stitch, radial extract
  obstruction_engine.py ← CSV obstacles injected into radial profiles
  visibility_engine.py  ← horizon angle tracking, AGL multi-height, diffraction
  coverage_engine.py    ← polar→GeoJSON, coverage area, export
  dem_preprocessor.py   ← void detection, tiered fill, cache
  gui/
    main_window.py      ← QMainWindow, QThread worker, real backend calls
    control_panel.py    ← all input widgets + ComputationRequest dataclass
    map_view.py         ← QWebEngineView + custom Leaflet HTML (no Folium)
    polar_view.py       ← Matplotlib embedded polar diagram
data/dem/               ← SRTM tiles here; _filled.tif auto-created
data/obstructions/      ← CSV obstacle files
tests/
```

---

## KEY IMPLEMENTATION RULES

- **float64 everywhere** in computation. Never float32 for range/elevation.
- **numpy vectorised** inside radial loop. No Python loops over range bins.
- `np.arctan2(y, x)` always — never `arctan(y/x)`.
- K-factor read from `earth_model.k` always — no literals.
- GUI computation in `QThread` always — never block main thread.
- Profile ranges must START at 0 (antenna bin); elevation[0] = site_elev_m.
- Out-of-DEM points → 0.0 m (sea level), NOT boundary pixel clipping.
- AGL target angle uses per-range `h_apparent + h_agl`, not `site_elev + h_agl`.
- GeoJSON polygon: first coordinate == last coordinate (closed ring).
- Polar diagram: `theta_zero='N'`, `theta_direction=-1` (compass convention).
- Map: use `LocalContentCanAccessRemoteUrls=True` in QWebEngineSettings.
- Map: hand-write Leaflet HTML with `L.polygon()` — do NOT use Folium GeoJson layer.
- Area: use `geod.geometry_area_perimeter(shapely.Polygon)` — not `polygon_area()`.

---

## TECH STACK

```bash
conda install -c conda-forge rasterio gdal pyproj geopandas scipy numpy pandas shapely
pip install PyQt6 PyQt6-WebEngine matplotlib
# folium no longer required — replaced by hand-written Leaflet HTML
```

**GUI:** PyQt6 + Matplotlib (polar) + custom Leaflet HTML/QWebEngineView (map). Dark theme `#1e1e2e`.
**DEM:** SRTM3 GeoTIFF primary. DTED Level 1/2 same code path (rasterio handles both).

---

## HEIGHT BAND COLOURS (Cambridge Pixel convention)
```python
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},
    100:  {"color": "#aacc00", "opacity": 0.40},
    500:  {"color": "#ff8800", "opacity": 0.40},
    1000: {"color": "#ff3300", "opacity": 0.35},
    3000: {"color": "#cc00ff", "opacity": 0.30},
}
```
Draw highest height first (largest area), lowest last (painted on top).

---

## VALIDATION CASES

### Tier 1 — No DEM needed (run on every change)
| ID | Test | Pass Condition |
|----|------|----------------|
| V1 | Flat synthetic terrain (all zeros, AGL mode) | Coverage limited by geometric horizon only — higher AGL = larger radius |
| V2 | K-factor ratio | `range(K=4/3) / range(K=1.0) ≈ sqrt(4/3)` |
| V3 | Single obstacle at 10km az=90° | az=90° blocked <10km; az=0°,180°,270° near max range |
| V4 | Diffraction guard 0° vs 0.5° | range(0.5°) ≤ range(0°) for blocked azimuth |
| V5 | Synthetic voids filled | fill_successful=True, max_deviation < 50m |
| V6 | **AGL circle test:** flat terrain, h_agl=50m | Coverage must vary with h_agl — 3000m AGL → larger than 50m AGL |

### Tier 2 — Real DEM
| ID | Site | What it proves |
|----|------|----------------|
| V7 | Dover, England 51.13°N 1.32°E | East/West ratio >1.5 (cliff blocks west) |
| V8 | Heraklion, Crete 35.33°N 25.13°E | North/South ratio >2.0 (island + mountain) |
| V9 | Ben Nevis, UK 56.8°N -5.0°E | 50m AGL coverage irregular (Highland terrain) |
| V10 | Al Jouf, Saudi Arabia 29.78°N 40.10°E | K-factor ratio holds on real flat desert |
| V11 | Canvey Island, UK 51.52°N 0.58°E | Obstruction injection masks Shard bearing |

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