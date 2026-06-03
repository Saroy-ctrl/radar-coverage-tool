# CLAUDE.md — Radar Coverage Analysis Tool

**Developer:** Saanann Roy | B.Tech CSE Sem 4 | Target: offline desktop MVP in 4–8 weeks
**References:** BEL paper (P.K. Gupta, V.K. Gupta) + Cambridge Pixel SPx Radar Coverage Tool

---

## WHAT TO BUILD
Offline desktop app: load SRTM/DTED terrain → compute terrain-masked radar coverage for multiple flight heights → display coloured coverage overlay on map + polar OVD diagram → export PNG/GeoTIFF/GeoJSON. Zero internet during operation.

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
Point visible if target_angle > horizon_angle at that range.

### Multi-Height (compute terrain ONCE, query per height)
```
target_angle[i] = atan2(H - curvature_correction(range[i]), range[i])
blocked = first index where target_angle < horizon
max_range = min(ranges[blocked], R_total(H), max_instrumented_range)
```

### Geodesy — NEVER flat Earth (>1% error beyond 10km)
Use `pyproj.Geod(ellps='WGS84')` for all distance/bearing. Use Haversine formula per BEL paper.

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

## ARCHITECTURE (build in this order)

```
src/
  dem_preprocessor.py   ← void detection, tiered fill, cache
  earth_model.py        ← K-factor, R_eff, curvature, horizon range
  terrain_engine.py     ← DEM load (calls preprocessor), tile stitch, radial extract
  obstruction_engine.py ← CSV obstacles injected into radial profiles
  visibility_engine.py  ← horizon angle tracking, multi-height, diffraction
  coverage_engine.py    ← polar→GeoJSON, coverage area, export
  gui/
    main_window.py      ← QMainWindow, QThread worker, layout
    control_panel.py    ← all input widgets
    map_view.py         ← QWebEngineView + Folium map
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
- After `extract_radial_profile`: assert `np.isnan(elevations).sum() == 0`.
- GeoJSON polygon: first coordinate == last coordinate (closed ring).
- Polar diagram: `theta_zero='N'`, `theta_direction=-1` (compass convention).

---

## TECH STACK

```bash
conda install -c conda-forge rasterio gdal pyproj geopandas scipy numpy pandas shapely
pip install PyQt6 PyQt6-WebEngine matplotlib folium branca
```

**GUI:** PyQt6 + Matplotlib (polar) + Folium/QWebEngineView (map). Dark theme `#1e1e2e`.
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
| V1 | Flat synthetic terrain, all zeros | Perfect circle ±1% of `R_total(h_antenna, H)` |
| V2 | K-factor ratio | `range(K=4/3) / range(K=1.0) = sqrt(4/3) ± 0.1%` |
| V3 | Single obstacle at 10km az=90° | az=90° blocked <10km; az=0°,180°,270° > 50km |
| V4 | Diffraction guard 0° vs 0.5° | range(0.5°) ≤ range(0°) for blocked azimuth |
| V5 | Synthetic voids filled | fill_successful=True, max_deviation < 50m |

### Tier 2 — Real DEM (skip if tile unavailable, all from srtm.csi.cgiar.org)
| ID | Site | Tile | What it proves |
|----|------|------|----------------|
| V6 | Den Helder, Netherlands 52.9°N 4.7°E | N52E004 | Flat+sea, near-circular |
| V7 | Dover, England 51.13°N 1.32°E | N51E001 | East/West ratio >1.5 (cliff AMSL) |
| V8 | Heraklion, Crete 35.33°N 25.13°E | N35E025 | North/South ratio >2.0 (island+mountain) |
| V9 | Lhasa, Tibet 29.65°N 91.13°E | N29E091 | Site elev 3500–3800m, not perfect circle |
| V10 | Al Jouf, Saudi Arabia 29.78°N 40.10°E | N29E040 | K-factor ratio holds on real flat desert |
| V11 | Canvey Island, UK 51.52°N 0.58°E | N51E000 | Obstruction injection masks Shard bearing |

**Priority if limited tiles:** V7 (catches AMSL bugs) → V10 (confirms K wiring) → V11 (confirms obstructions).

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
- Log void fill stats on every DEM load