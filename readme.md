# CLAUDE.md — Radar Site Selection & Coverage Analysis Tool
## Project Intelligence File for Claude Code

---

## WHO YOU ARE WORKING WITH

**Developer:** Saanann Roy — B.Tech CSE student (Semester 4, graduating 2028), India.
**Goal:** Build a technically convincing, offline desktop MVP for radar coverage and site selection analysis.
**Timeline:** 4–8 weeks.
**Reference tool:** Cambridge Pixel SPx Radar Coverage Tool (see screenshot in project).
**Reference paper:** "Radar Visibility Diagram for Site Selection" — P.K. Gupta & Vinay Kumar Gupta, BEL Ghaziabad.

---

## WHAT THIS PROJECT IS

An **offline desktop application** that:
1. Takes a radar site location and parameters as input
2. Loads terrain elevation data (SRTM/DTED) from local disk
3. Preprocesses DEM data (void fill, quality check) before any analysis
4. Computes terrain-masked radar coverage for multiple target flight heights simultaneously
5. Displays the result as a coloured coverage overlay on a real map (like Cambridge Pixel's tool)
6. Also displays a classic polar OVD/RVD diagram (like the BEL paper's Figure 4)
7. Exports results as PNG, GeoTIFF, GeoJSON

**100% offline. No internet required during operation. No cloud. No web backend.**

---

## THE CORE SCIENTIFIC PROBLEM

A radar cannot see targets hidden behind terrain (hills, mountains, buildings). The goal is to compute, for a given radar site:

> *"For each direction (azimuth) around the radar, what is the maximum range at which a target flying at height H can be detected, given terrain obstructions and Earth curvature?"*

This must be computed for multiple target heights simultaneously (e.g., 50m, 100m, 500m, 1000m, 3000m AGL).

---

## KEY PHYSICS — READ THIS CAREFULLY

### 1. Earth Curvature and K-Factor

Radio waves do NOT travel in straight lines — the atmosphere bends them. This is modelled by replacing the real Earth radius with an **effective Earth radius**:

```
R_eff = K * R_earth
R_earth = 6,371,000 metres
K = 4/3 (standard atmosphere, default)
K = 1.0 (optical/visual, no bending)
K = user-defined (configurable, NEVER hardcode K)
```

**Curvature correction** — terrain at ground distance `d` from radar appears lower than flat-earth would suggest:

```
delta_h = d² / (2 * R_eff)
h_apparent = h_terrain_AMSL - delta_h
```

At 50 km range, K=4/3: correction ≈ 94 metres. This is NOT negligible.

### 2. Radar Horizon Range

Maximum range at which a radar at height `h_r` can see a target at height `h_t`:

```
R_horizon = sqrt(2 * K * R_earth * h_r) + sqrt(2 * K * R_earth * h_t)
```

Units: all in metres, result in metres. Convert to km for display.

### 3. Obstruction Angle (The Core Algorithm)

For each terrain point along a radial (ray from radar):

```
obstruction_angle = atan2(h_apparent - h_antenna_AMSL, d)
```

Where:
- `h_apparent` = terrain height AMSL minus curvature correction
- `h_antenna_AMSL` = site elevation + mast height + antenna height
- `d` = ground range in metres from radar to that point

A point is **blocked** if its obstruction_angle exceeds the maximum obstruction angle seen at any closer range. This is the **horizon angle tracking** algorithm.

### 4. Diffraction Guard Angle

Electromagnetic waves bend around obstacles. To account for this conservatively, add a small guard angle to every obstruction angle:

```
effective_obstruction_angle = obstruction_angle + diffraction_guard_angle
diffraction_guard_angle = 0.5 degrees = 0.00873 radians (default, configurable)
```

This makes coverage estimates slightly conservative (smaller coverage = safer for military use).

### 5. Multi-Height Strategy (CRITICAL FOR PERFORMANCE)

**The key insight:** terrain profile along a radial is computed ONCE. All flight heights reuse it.

```
Step 1 (once per azimuth):
  - Extract terrain elevation profile along radial
  - Apply curvature correction to each elevation
  - Inject local obstacles at their range bins
  - Apply diffraction guard
  - Compute cumulative maximum obstruction angle array: horizon_angles[i] = max(angles[0..i])
  - Store this array as the RadialProfile

Step 2 (per flight height, fast):
  - For height H, target's elevation angle at range d = atan2(H - curvature(d), d)
  - Find first range bin where target angle < horizon_angles → that's the blockage range
  - Max visible range = min(blockage_range, radar_horizon_range(H), max_instrumented_range)
```

**Result:** N flight heights cost only slightly more than 1 flight height.

### 6. Distance and Bearing (Geodetic — NOT flat Earth)

Use Haversine formula for range, Spherical Law of Cosines for bearing.
**NEVER use flat-Earth approximations.** Errors exceed 1% beyond 10 km.

```
Haversine:
  Δlong = lon2 - lon1
  Δlat  = lat2 - lat1
  a = sin²(Δlat/2) + cos(lat1)*cos(lat2)*sin²(Δlong/2)
  c = 2 * atan2(√a, √(1-a))
  Range = R_earth * c

Bearing:
  θ = atan2(sin(Δlong)*cos(lat2), cos(lat1)*sin(lat2) - sin(lat1)*cos(lat2)*cos(Δlong))
```

Use `pyproj.Geod` — it handles all of this correctly and is more accurate than manual implementation.

---

## SRTM VOID PREPROCESSING — MANDATORY, NON-NEGOTIABLE

### Why This Is Critical

SRTM data contains voids (missing elevation values) marked as -32768 (int16) or a dataset-specific NoData value. These voids exist because:

- The Space Shuttle radar could not penetrate dense vegetation in some tropical regions
- Steep terrain caused radar shadow (the shuttle flew at a fixed angle)
- Water bodies returned no coherent signal
- Some tiles over the Himalayas, Andes, and dense jungles have significant void regions

**If you do NOT fill voids:**
- `h_apparent = -32768 - curvature_correction` → enormous negative number
- `obstruction_angle = atan2(-32768 - h_antenna, range)` → large negative angle
- Cumulative max will correctly not block anything, BUT the void itself silently drops to sea floor
- Radials crossing void pixels will show falsely long range (unblocked) through terrain
- This is a silent scientific error — the program runs but produces wrong results

**Void filling is NOT optional. It must happen before any analysis.**

---

### SRTM VOID FILLING PIPELINE

Implement in `src/dem_preprocessor.py` as a standalone module. Run ONCE when a DEM is first loaded, cache the result.

#### Step 1: Detect Voids

```python
import rasterio
import numpy as np

SRTM_NODATA_INT16 = -32768
SRTM_NODATA_FLOAT = -9999.0

def detect_voids(dem_array: np.ndarray, nodata_value: float) -> np.ndarray:
    """
    Returns boolean mask: True where pixel is void/invalid.
    Handles both integer NoData (-32768) and float NoData (-9999).
    Also catches physically impossible values (elevation < -500m or > 9000m).
    """
    void_mask = (dem_array == nodata_value)
    void_mask |= (dem_array < -500)    # below Dead Sea level — impossible on land
    void_mask |= (dem_array > 9000)    # above Everest — impossible
    return void_mask
```

Report void statistics before filling:
```python
void_count = np.sum(void_mask)
void_pct = 100.0 * void_count / dem_array.size
print(f"[DEM] Voids detected: {void_count} pixels ({void_pct:.2f}%)")
if void_pct > 20.0:
    warnings.warn(f"DEM has {void_pct:.1f}% voids — results may be unreliable in void regions")
```

#### Step 2: Fill Strategy (Tiered by Void Size)

Use a **tiered approach** — small voids get precise fills, large voids get coarser fills:

```
TIER 1: Small voids (< 10 connected pixels)
  Method: scipy.ndimage.generic_filter with np.nanmean, radius=3
  This handles single-pixel NoData (sensor glitches, isolated water pixels)
  Fast, accurate for small gaps

TIER 2: Medium voids (10–100 connected pixels)
  Method: scipy.interpolate.griddata with 'linear' method
  Use surrounding valid pixels as control points
  Handles small lakes, narrow river valleys

TIER 3: Large voids (> 100 connected pixels)
  Method: scipy.interpolate.griddata with 'nearest' neighbour fallback
  For large mountain shadow voids where linear interpolation would be wrong
  Nearest-neighbour is geomorphologically safer than inventing elevation contours

FINAL: Any remaining NaN after all tiers
  Method: np.nan_to_num(dem, nan=0.0)
  Last resort — zero elevation (sea level) for completely isolated regions
  Log a warning for each instance
```

#### Step 3: Smoothing After Fill (Optional but Recommended)

Large filled voids can create sharp discontinuities at fill boundaries. Apply a gentle Gaussian smooth ONLY to the filled pixels and a small buffer around them:

```python
from scipy.ndimage import gaussian_filter, binary_dilation

# Create buffer mask around filled pixels
fill_buffer = binary_dilation(void_mask, iterations=3)

# Apply Gaussian smooth (sigma=1.0) to original DEM
smoothed = gaussian_filter(dem_filled.astype(np.float64), sigma=1.0)

# Blend: use smoothed only in the fill buffer zone, keep original elsewhere
dem_final = np.where(fill_buffer, smoothed, dem_filled)
```

#### Step 4: Verify Fill Quality

After filling, run these checks:

```python
def verify_fill_quality(original: np.ndarray, filled: np.ndarray,
                         void_mask: np.ndarray) -> dict:
    """
    Returns quality report dict.
    Checks:
    - No NaN or -32768 values remain
    - Fill values are within ±500m of surrounding valid pixels (no wild extrapolation)
    - Fill percentage actually decreased to 0
    """
    remaining_voids = np.sum(np.isnan(filled) | (filled == -32768))
    
    # Check fill plausibility: filled values vs local neighbourhood average
    if void_mask.any():
        filled_vals = filled[void_mask]
        # Get local mean from a dilated neighbourhood excluding original voids
        neighbourhood = gaussian_filter(
            np.where(void_mask, np.nan, original.astype(float)), sigma=5
        )
        local_means = neighbourhood[void_mask]
        valid_comparisons = ~np.isnan(local_means)
        if valid_comparisons.any():
            deviations = np.abs(filled_vals[valid_comparisons] - local_means[valid_comparisons])
            max_deviation = np.nanmax(deviations)
            mean_deviation = np.nanmean(deviations)
        else:
            max_deviation = mean_deviation = 0.0
    else:
        max_deviation = mean_deviation = 0.0
    
    return {
        "remaining_voids": int(remaining_voids),
        "fill_successful": remaining_voids == 0,
        "max_fill_deviation_m": float(max_deviation),
        "mean_fill_deviation_m": float(mean_deviation),
        "fill_plausible": max_deviation < 500.0
    }
```

#### Step 5: Cache the Preprocessed DEM

After void-filling, save the result to disk as a GeoTIFF so it is NOT recomputed on next launch:

```python
def save_preprocessed_dem(dem_array: np.ndarray, profile: dict,
                           output_path: str) -> None:
    """
    Save void-filled DEM as float32 GeoTIFF with updated NoData=-9999.
    Filename convention: original_name + '_filled.tif'
    e.g. N34E077.tif → N34E077_filled.tif
    """
    profile_out = profile.copy()
    profile_out.update({
        'dtype': 'float32',
        'nodata': -9999.0,
        'compress': 'lzw'  # lossless compression, reduces file size ~40%
    })
    with rasterio.open(output_path, 'w', **profile_out) as dst:
        dst.write(dem_array.astype(np.float32), 1)
```

On next launch: check if `_filled.tif` exists → load it directly (skip preprocessing).

---

### DEM PREPROCESSING INTEGRATION INTO TERRAIN ENGINE

The `TerrainEngine.__init__()` must call the preprocessor automatically:

```python
class TerrainEngine:
    def __init__(self, dem_paths: list[str]):
        """
        Load, preprocess (void-fill), stitch, and cache DEM data.
        Preprocessing is automatic — caller does not need to manage it.
        """
        preprocessed_arrays = []
        for path in dem_paths:
            filled_path = path.replace('.tif', '_filled.tif')
            if os.path.exists(filled_path):
                # Load cached preprocessed version
                with rasterio.open(filled_path) as src:
                    arr = src.read(1).astype(np.float64)
                    # ...store transform, crs, etc.
            else:
                # Preprocess and cache
                preprocessor = DEMPreprocessor(path)
                arr, profile = preprocessor.run()  # full pipeline
                save_preprocessed_dem(arr, profile, filled_path)
            preprocessed_arrays.append(arr)
        
        # Stitch tiles if multiple
        self.stitched_dem = stitch_tiles(preprocessed_arrays)
```

---

### VOID PREPROCESSING: WHAT TO TELL THE USER

In the GUI, show a preprocessing status indicator:

- When loading a new DEM: show `QProgressDialog` with message "Preprocessing terrain data — filling voids..."
- After preprocessing: show in status bar: `"DEM loaded: N34E077.tif — 0.3% voids filled, quality: GOOD"`
- If void% > 20%: show `QMessageBox.warning`: "Warning: DEM has high void content (X%). Coverage results may be inaccurate in affected regions. Consider obtaining a better DEM for this area."
- If fill quality check fails: show warning with max_fill_deviation

---

## ARCHITECTURE — 7 MODULES

```
radar_coverage_tool/
├── src/
│   ├── earth_model.py        ← K-factor, R_eff, curvature corrections, horizon range
│   ├── dem_preprocessor.py   ← SRTM void detection, filling, caching, quality check
│   ├── terrain_engine.py     ← DEM loading (calls preprocessor), tile stitching, radial extraction
│   ├── obstruction_engine.py ← CSV point obstacles, injection into radial profiles
│   ├── visibility_engine.py  ← Horizon angle tracking, multi-height, diffraction guard
│   ├── coverage_engine.py    ← Polar→GeoJSON, coverage area stats, rasterization
│   └── gui/
│       ├── main_window.py    ← QMainWindow, layout, signals
│       ├── control_panel.py  ← Input widgets (lat/lon, heights, K-factor, etc.)
│       ├── map_view.py       ← QWebEngineView + Folium/Leaflet map with GeoJSON overlay
│       └── polar_view.py     ← Matplotlib embedded polar OVD/RVD diagram
├── data/
│   ├── dem/                  ← User places SRTM GeoTIFF files here
│   │                           Preprocessed _filled.tif files are auto-created here
│   ├── tiles/                ← Pre-cached offline map tiles (MBTiles or tile cache)
│   └── obstructions/         ← CSV obstacle files (sample included)
├── tests/
│   ├── test_earth_model.py
│   ├── test_dem_preprocessor.py   ← void fill tests with synthetic arrays
│   ├── test_terrain.py
│   ├── test_visibility.py
│   └── tests_geographic_validation/
│       ├── validate_flat_synthetic.py      ← no DEM needed
│       ├── validate_k_factor_ratio.py      ← no DEM needed
│       ├── validate_den_helder.py          ← Netherlands coast (N52E004)
│       ├── validate_dover.py               ← Dover cliffs (N51E001)
│       ├── validate_crete.py               ← Crete island (N35E025)
│       ├── validate_tibetan_plateau.py     ← Lhasa Tibet (N29E091)
│       ├── validate_al_jouf_desert.py      ← Saudi Arabia (N29E040)
│       └── validate_canvey_island.py       ← London urban (N51E000)
├── requirements.txt
├── README.md
└── CLAUDE.md                 ← this file
```

---

## MODULE RESPONSIBILITIES (BUILD IN THIS ORDER)

### Module 0: `dem_preprocessor.py`
**Build before terrain_engine. Everything depends on clean data.**

```python
class DEMPreprocessor:
    """
    Full SRTM/DEM void-filling pipeline.
    Call run() to execute all steps in order.
    """
    def __init__(self, dem_path: str):
        self.dem_path = dem_path

    def run(self) -> tuple[np.ndarray, dict]:
        """
        Execute full pipeline:
        1. load() → raw array + rasterio profile
        2. detect_voids() → void mask + statistics
        3. fill_voids_tiered() → filled array
        4. smooth_fill_boundaries() → smoothed array
        5. verify_fill_quality() → quality report
        6. Returns (filled_array_float64, rasterio_profile)
        """

    def load(self) -> tuple[np.ndarray, dict]:
        """Load raw DEM, return (array as float64, rasterio profile)."""

    def detect_voids(self, dem: np.ndarray, nodata: float) -> np.ndarray:
        """Return boolean void mask. See SRTM VOID PREPROCESSING section."""

    def fill_voids_tiered(self, dem: np.ndarray, void_mask: np.ndarray) -> np.ndarray:
        """
        Tier 1: small voids → nanmean filter
        Tier 2: medium voids → linear interpolation
        Tier 3: large voids → nearest neighbour
        Fallback: nan_to_num(0.0)
        """

    def smooth_fill_boundaries(self, dem: np.ndarray, void_mask: np.ndarray) -> np.ndarray:
        """Gentle Gaussian smooth at fill boundaries only."""

    def verify_fill_quality(self, original: np.ndarray, filled: np.ndarray,
                             void_mask: np.ndarray) -> dict:
        """Return quality report dict. See SRTM VOID PREPROCESSING section."""
```

---

### Module 1: `earth_model.py`
**Build after dem_preprocessor. All computation depends on this.**

```python
class EffectiveEarthModel:
    R_EARTH = 6_371_000.0  # metres

    def __init__(self, k_factor: float = 4/3):
        self.k = k_factor
        self.r_eff = k_factor * self.R_EARTH

    def curvature_correction(self, range_m: float) -> float:
        """Height correction (metres) for terrain point at ground range range_m."""
        return (range_m ** 2) / (2 * self.r_eff)

    def radar_horizon_range(self, antenna_height_m: float) -> float:
        """Max range (metres) to horizon for antenna at height_m AMSL above terrain."""
        return (2 * self.r_eff * antenna_height_m) ** 0.5

    def total_visibility_range(self, h_radar_m: float, h_target_m: float) -> float:
        """Total radar line-of-sight range (metres) with no terrain obstruction."""
        return self.radar_horizon_range(h_radar_m) + self.radar_horizon_range(h_target_m)
```

Use float64 throughout. Never float32 for range/elevation calculations.

---

### Module 2: `terrain_engine.py`
**Core DEM interface. Calls DEMPreprocessor automatically.**

Responsibilities:
- Call `DEMPreprocessor.run()` for each tile, cache result as `_filled.tif`
- Load preprocessed tiles with rasterio
- Support SRTM3 GeoTIFF (primary) and DTED Level 1/2 (same code path)
- Stitch multiple tiles covering the analysis bounding box
- `extract_radial_profile(azimuth_deg, n_bins, max_range_m)` → arrays of (ranges, elevations)
  - Use `scipy.ndimage.map_coordinates` for fast bilinear interpolation — NOT a Python loop
  - Return (ranges_m: ndarray[N], elevations_m: ndarray[N])
  - After extraction: assert no NaN, no -32768, no values < -500 (preprocessor should have fixed these)

**Critical:** DEM stays in native WGS84. Do NOT reproject.

---

### Module 3: `obstruction_engine.py`
**Local obstacle injection.**

Input CSV format (provide sample file in data/obstructions/sample_obstructions.csv):
```
lat,lon,height_amsl_m,type,description
28.6139,77.2090,350.0,tower,Communication Tower Delhi
34.1526,77.5771,3650.0,building,Structure LEH
```

Responsibilities:
- Load CSV into list of ObstaclePoint objects
- `get_obstacles_on_radial(radar_lat, radar_lon, azimuth_deg, azimuth_tolerance_deg=0.5)` → list of (range_m, height_amsl_m) tuples
- For each obstacle on a radial, find its range bin index and set terrain elevation to max(terrain, obstacle_height)

---

### Module 4: `visibility_engine.py`
**The computational heart of the application.**

```python
@dataclass
class RadialProfile:
    azimuth_deg: float
    ranges_m: np.ndarray           # shape (N,), float64
    terrain_heights_m: np.ndarray  # shape (N,), float64, curvature-corrected
    horizon_angles_rad: np.ndarray # shape (N,), cumulative max obstruction angle

@dataclass
class VisibilityResult:
    height_to_ranges: dict[float, np.ndarray]  # {50.0: ndarray[720], ...}
    radial_profiles: list[RadialProfile]
    radar_site: tuple[float, float]
    computation_params: dict
```

Algorithm per azimuth:
```
1. terrain_engine.extract_radial_profile(az, n_bins, max_range)
   → ASSERT: no NaN in elevations (if any found, raise ValueError with location info)

2. curvature_corrections = earth_model.curvature_correction(ranges_m)  # vectorised
   h_corrected = terrain_elevs - curvature_corrections

3. obstruction_engine: inject obstacles → update h_corrected

4. obs_angles = np.arctan2(h_corrected - h_antenna_amsl, ranges_m)
5. obs_angles += diffraction_guard_rad
6. horizon_angles = np.maximum.accumulate(obs_angles)
7. Store as RadialProfile

For each flight_height H:
  target_h_corrected = H - curvature_corrections
  target_angles = np.arctan2(target_h_corrected, ranges_m)
  blocked = np.where(target_angles < horizon_angles)[0]
  max_range = ranges_m[blocked[0]] if blocked.size > 0 else ranges_m[-1]
  cap at total_visibility_range(H) and max_range_m
```

**Performance requirement:** 720 azimuths × 500 bins × 6 heights < 30 seconds. Vectorised NumPy only.

---

### Module 5: `coverage_engine.py`
**Converts visibility results to map-renderable outputs.**

Responsibilities:
- `generate_geojson(result: VisibilityResult)` → GeoJSON FeatureCollection
- `compute_coverage_stats(result)` → coverage area (km²) per height
- `export_geotiff(result, output_path)` → GeoTIFF raster
- `export_png(polar_fig, output_path)` → PNG

```python
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},
    100:  {"color": "#aacc00", "opacity": 0.40},
    500:  {"color": "#ff8800", "opacity": 0.40},
    1000: {"color": "#ff3300", "opacity": 0.35},
    3000: {"color": "#cc00ff", "opacity": 0.30},
}
DEFAULT_COLOR = {"color": "#4a9eff", "opacity": 0.40}
```

---

### Module 6: GUI
(Same as before — see GUI section below)

---

## GUI REQUIREMENTS

### Layout (match Cambridge Pixel)

```
QMainWindow
├── Central Widget: QSplitter (horizontal)
│   ├── LEFT: ControlPanel (fixed width ~300px)
│   └── RIGHT: QSplitter (vertical)
│       ├── TOP (70%): MapView (QWebEngineView)
│       └── BOTTOM (30%): PolarView (Matplotlib canvas)
├── Status bar: computation progress + DEM quality info
└── Menu bar: File (Open DEM, Save Results, Export), Help (About)
```

### Control Panel Widgets

```
Position Group:
  - Latitude (QDoubleSpinBox, -90 to 90, 6 decimal places)
  - Longitude (QDoubleSpinBox, -180 to 180, 6 decimal places)

Radar Parameters Group:
  - Site Elevation AMSL (m) — auto-read from DEM or manual override
  - Mast Height (m)
  - Antenna Height (m)
  - Maximum Range (km)
  - K-Factor (QDoubleSpinBox, 0.5 to 3.0, default 1.333)
  - Diffraction Guard Angle (degrees, default 0.5)

Target Heights Group:
  - QTableWidget: [checkbox, height (m), color swatch, label]
  - Default rows: 50m, 100m, 500m, 1000m, 3000m
  - Add/Remove row buttons

Local Obstructions Group:
  - QLineEdit + Browse button → CSV path
  - "Load CSV" button

DEM Source Group:
  - QLineEdit + Browse button
  - Format: SRTM3 / DTED-1 / DTED-2 / Auto-detect
  - "Preprocess DEM" button (shows void stats before compute)

Compute Button (green, prominent)
QProgressBar

Export Group (enabled after compute):
  - Save GeoJSON, Save GeoTIFF, Save PNG buttons
```

### Visual Style

```
Dark theme: background #1e1e2e, panels #2a2a3e, accents #4a9eff
Coverage colors: see HEIGHT_BAND_COLORS above
Higher height layers drawn FIRST → lower heights paint on top (more restrictive on top)
```

---

## DATA FORMATS

### Input: SRTM3 GeoTIFF
- File naming: `srtm_XX_YY.tif` or `N34E077.tif`
- CRS: WGS84 (EPSG:4326)
- Resolution: ~90m (3 arc-second)
- Tile size: 1°×1° = 1201×1201 pixels
- **NoData value: -32768 (MUST be filled before analysis)**
- Preprocessed cache: saved as `N34E077_filled.tif` in same folder

### Input: DTED
- Extensions: `.dt0`, `.dt1`, `.dt2`
- Rasterio reads natively — same preprocessing pipeline applies
- **DTED NoData varies by file — always read from rasterio profile**

### Input: Obstruction CSV
```
lat,lon,height_amsl_m,type,description
```

### Output: GeoJSON
```json
{
  "type": "FeatureCollection",
  "properties": {
    "radar_lat": 34.1526,
    "radar_lon": 77.5771,
    "k_factor": 1.333,
    "dem_void_fill_pct": 0.3,
    "computation_time_s": 4.2
  },
  "features": [
    {
      "type": "Feature",
      "properties": {"height_m": 500, "color": "#FF8800", "coverage_area_km2": 12450.3},
      "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], ...]]}
    }
  ]
}
```

---

## NUMERICAL REQUIREMENTS

- All elevation and range calculations: **float64** (never float32)
- Angle calculations: radians internally, degrees for display only
- All atan2 calls: `np.arctan2(y, x)` — never `np.arctan(y/x)` (division by zero risk)
- Curvature corrections: **always applied** — never optional, never skip
- K-factor: **always read from EarthModel** — never use a literal 1.333 anywhere in code
- Radial resolution: configurable, default 0.5° (720 azimuths)
- Range bin size: max_range / n_bins, default n_bins=500
- **After void fill: ZERO NaN or -32768 values allowed in any array used for computation**

---

## VALIDATION CASES

### TIER 1 — NO DEM REQUIRED (run on every code change, always fast)

**Test V1: Flat Synthetic Terrain**
Purpose: Validates K-factor, curvature, and horizon formulas.
Setup: All elevations = 0m (sea level). Radar at sea level with 5m antenna.
Expected: Perfect circle. Radius at height H = `total_visibility_range(5m, H)`
Tolerance: < 1% (discretisation noise only)
Pass condition: All 720 azimuths give max_range within 1% of expected.

**Test V2: K-Factor Ratio**
Purpose: Proves K-factor is actually wired — not hardcoded.
Setup: Flat terrain, same parameters. Run with K=1.0 and K=4/3.
Expected: range(K=4/3) / range(K=1.0) = sqrt(4/3) ≈ 1.1547
Tolerance: < 0.1%
Pass condition: The ratio matches within tolerance for all heights.

**Test V3: Single Obstacle Blockage**
Purpose: Validates obstruction injection and horizon angle algorithm.
Setup: Flat terrain + obstacle at 10km azimuth=90°, height = h_antenna + 100m.
Expected: Azimuth 90° blocked at ≤10km. Azimuths 0°, 180°, 270° unaffected.
Pass condition: Blocked azimuth < 10km. Adjacent azimuths (89°, 91°) may or may not be affected depending on azimuth tolerance. At azimuths ±45° from obstacle, range must be > 50km.

**Test V4: Diffraction Guard Sensitivity**
Purpose: Verifies diffraction guard actually reduces range.
Setup: Single obstacle. Run with guard=0° and guard=0.5°.
Expected: guard=0.5° gives SMALLER max_range (more conservative).
Pass condition: range(guard=0.5°) < range(guard=0°) for the blocked azimuth.

**Test V5: Void Fill Correctness**
Purpose: Validates DEMPreprocessor on synthetic voids.
Setup: Create 200×200 float array with smooth sine-wave terrain. Inject known voids at specific pixels. Run DEMPreprocessor.
Expected: After fill, no NaN or -32768 remains. Fill values within ±50m of true synthetic values (known since we generated them).
Pass condition: verify_fill_quality() returns fill_successful=True and max_fill_deviation_m < 50.


### TIER 2 — REAL DEM REQUIRED (skip if tile not available, run before release)

Download tiles from: https://srtm.csi.cgiar.org/srtmdata/

---

**Test V6: Den Helder, Netherlands — Offshore Flat**
Tile: N52E004
Coordinates: 52.9°N, 4.7°E (Den Helder naval base)
Site elevation: ~0m AMSL, antenna 10m
What it tests: Near-zero terrain (flat Netherlands polders) + open North Sea.
Expected behaviour:
  - Westward (sea): near-theoretical maximum coverage
  - Eastward (land): slight reduction from Dutch terrain (~5–10m polders)
  - Coverage should be very nearly circular — deviation < 15% between all azimuths
Pass condition: max_range(West, 270°) > 0.9 × theoretical_maximum. Void fill % should be near 0 (flat coastal terrain has few SRTM voids).

---

**Test V7: Dover Cliffs, England — Coastal Cliff Asymmetry**
Tile: N51E001
Coordinates: 51.13°N, 1.32°E (Dover Castle)
Site elevation: ~114m AMSL (White Cliffs), antenna 5m
What it tests:
  - Radar sitting ON a cliff above sea level
  - East (English Channel toward France): very long open-sea range
  - West (UK inland): terrain masking from North Downs and South Downs
Expected behaviour:
  - East range >> West range
  - East range should approach theoretical max for 114m elevation
  - Coverage clearly asymmetric
Pass condition:
  avg_range(East: 60–120°) > 1.5 × avg_range(West: 240–300°)
  This asymmetry ratio proves AMSL site elevation is handled correctly.

---

**Test V8: Heraklion, Crete — Island Radar**
Tile: N35E025
Coordinates: 35.33°N, 25.13°E (Heraklion Airport area)
Site elevation: ~30m AMSL, antenna 10m
What it tests:
  - Sea on all sides at long range → North Sea coverage should be excellent
  - Psiloritis/Ida mountain range (2456m) immediately to the south-southwest
  - Mixed coastal + mountain terrain in one scene
Expected behaviour:
  - North: open Aegean Sea → large range
  - South: heavily blocked by Cretan mountains → much shorter range
  - Strong directional asymmetry (North >> South)
Pass condition:
  avg_range(North: 330–30°) > 2.0 × avg_range(South: 150–210°)
  Void fill may be > 1% over steep Cretan mountains — check and log.

---

**Test V9: Tibetan Plateau, Near Lhasa — High Base Elevation**
Tile: N29E091
Coordinates: 29.65°N, 91.13°E
Site elevation: ~3650m AMSL (read from DEM), antenna 5m
What it tests:
  - Radar at high altitude on plateau — base elevation is very high
  - Surrounding plateau also at ~3600-4200m
  - Small relative height differences dominate (not absolute elevation)
  - Tests correct subtraction of base elevation in obstruction angle computation
Expected behaviour:
  - Open plateau: decent range
  - Mountain ridges surrounding plateau: strong masking
  - If base elevation is handled wrongly (ignored), ALL coverage will be incorrectly large
Pass condition:
  site_elev = terrain.get_elevation_at(29.65, 91.13) → must be 3500–3800m (sanity check)
  Coverage must NOT be a perfect circle (terrain variation must produce asymmetry)
  avg_range should be 30–80km for 100m AGL targets (plateau is not perfectly flat)
  Void fill here may be significant — log carefully.

---

**Test V10: Al Jouf Desert, Saudi Arabia — K-Factor Sweep**
Tile: N29E040
Coordinates: 29.78°N, 40.10°E
Site elevation: ~700m AMSL (desert plateau), antenna 5m
What it tests:
  - Near-flat desert terrain
  - Almost no terrain masking in any direction
  - Ideal site to test K-factor sensitivity in isolation
Expected behaviour:
  - Coverage approaches perfect circle in all directions
  - K-factor sweep (1.0, 4/3, 2.0) produces clearly different range circles
  - Ratio between K=4/3 and K=1.0 circles ≈ sqrt(4/3) = 1.1547
Pass condition:
  Coverage circle deviation < 10% (desert is flat, small deviation expected)
  K-factor ratio test: ratio within 2% of sqrt(4/3)
  This is the key K-factor wiring validation with real terrain.

---

**Test V11: Canvey Island, Essex, UK — Urban Obstruction Injection**
Tile: N51E000
Coordinates: 51.52°N, 0.58°E (Canvey Island, Thames Estuary)
Site elevation: ~2m AMSL, antenna 10m
What it tests:
  - Flat terrain (Thames estuary)
  - Load obstruction CSV with known tall London structures
  - Verifies obstruction injection creates specific masked wedges

Obstruction CSV for this test (save as tests/data/london_obstructions.csv):
```
lat,lon,height_amsl_m,type,description
51.5045,−0.0196,310.0,skyscraper,The Shard
51.5034,−0.0009,235.0,skyscraper,Canary Wharf Tower
51.5074,−0.0877,180.0,tower,BT Tower London
```

Expected behaviour:
  - Without obstructions: near-perfect circle (flat terrain)
  - With obstructions: specific azimuths toward London blocked at obstruction range
  - The Shard at bearing ~285° from Canvey: should create masked wedge
Pass condition:
  bearing_to_shard = geodetic_bearing(canvey_lat, canvey_lon, 51.5045, -0.0196)
  range_with_obs[bearing_to_shard] < range_without_obs[bearing_to_shard]
  range_without_obs forms near-circle (deviation < 15%)

---

### VALIDATION PRIORITY ORDER

If you only have time for a subset:

**Must run (no files needed):**
V1, V2, V3, V4, V5

**Run first with real DEM:**
V7 (Dover) — catches AMSL elevation bugs most reliably
V10 (Al Jouf) — proves K-factor wiring in real terrain context
V11 (Canvey + obstructions) — proves obstruction engine end-to-end

**Run before claiming "validated":**
All 11 tests pass.

---

## DEPENDENCIES

```
# requirements.txt
PyQt6>=6.6.0
PyQt6-WebEngine>=6.6.0
rasterio>=1.3.0
numpy>=1.26.0
scipy>=1.11.0
pyproj>=3.6.0
geopandas>=0.14.0
shapely>=2.0.0
matplotlib>=3.8.0
folium>=0.15.0
branca>=0.7.0
pandas>=2.0.0
```

**Install with conda for GDAL compatibility:**
```bash
conda create -n radar_coverage python=3.11
conda activate radar_coverage
conda install -c conda-forge rasterio gdal pyproj geopandas scipy numpy pandas shapely
pip install PyQt6 PyQt6-WebEngine matplotlib folium branca
```

---

## WHAT NOT TO BUILD (MVP SCOPE)

**DO NOT build:**
- Radar equation / detection range / RCS modelling
- Full ITU-R P.526 knife-edge diffraction (guard angle only)
- Propagation loss / atmospheric attenuation
- Multi-radar site comparison
- Real-time satellite DEM download
- Cloud sync, web API, network features of any kind
- Mobile or web version
- 3D visualization
- Animation / time-series
- UTM reprojection of DEM
- SRTM void fill using external datasets (use interpolation only)

**DO build:**
- Everything in the 7 modules above
- The GUI described above
- Export (PNG, GeoJSON, GeoTIFF)
- All 5 Tier 1 validation tests
- As many Tier 2 tests as tiles are available

---

## BUILD ORDER (STRICT)

1. `dem_preprocessor.py` + `test_dem_preprocessor.py` — synthetic void tests
2. `earth_model.py` + `test_earth_model.py` — validate formulas
3. `terrain_engine.py` + `test_terrain.py` — calls preprocessor, test with synthetic DEM
4. `obstruction_engine.py` — test manually
5. `visibility_engine.py` + `test_visibility.py` — V1, V2, V3, V4 must pass
6. `coverage_engine.py` — GeoJSON, open in QGIS to verify
7. GUI skeleton (`main_window.py`) — layout only
8. `polar_view.py` — Matplotlib polar with dummy data
9. `map_view.py` — Folium map with dummy GeoJSON
10. Wire GUI to pipeline
11. Run Tier 2 geographic validation tests
12. Polish: error handling, export buttons, status bar DEM quality info

**Do not skip steps. Do not build GUI before Tier 1 tests pass.**

---

## ERROR HANDLING REQUIREMENTS

- Missing DEM file: `QMessageBox.critical` with path and download instructions
- DEM doesn't cover analysis area: warn with required bounding box
- Void fill > 20%: `QMessageBox.warning` — results may be unreliable
- Void fill quality check fails (max_deviation > 500m): warn user specifically
- Any NaN in profile after preprocessing: raise ValueError (should never happen after fill)
- Computation > 60s: show progress bar + cancel button
- Invalid coordinates outside DEM bounds: validate on input, inline error

---

## COMMENTS AND CODE STYLE

- Every function: docstring with physical meaning, units, formula source
- Units in parameter names: `range_m`, `height_amsl_m`, `angle_rad`
- Formula citations: `# BEL paper Eq. 3` or `# ITU-R K-factor`
- No magic numbers — constants at module level with descriptive names
- Preprocessing functions: log void statistics at INFO level always

---

## SAMPLE DATA TO INCLUDE

`data/obstructions/sample_obstructions.csv`:
```
lat,lon,height_amsl_m,type,description
34.1600,77.5900,3350.0,tower,Sample Comm Tower
34.1400,77.5600,3280.0,building,Sample Building
```

`tests/data/london_obstructions.csv`:
```
lat,lon,height_amsl_m,type,description
51.5045,-0.0196,310.0,skyscraper,The Shard
51.5034,-0.0009,235.0,skyscraper,Canary Wharf Tower
51.5074,-0.0877,180.0,tower,BT Tower London
```

`data/dem/README.txt`:
```
Place SRTM3 GeoTIFF tiles here.
Standard naming: N34E077.tif (latitude then longitude, no spaces)

Download sources (all free):
  SRTM3 (90m): https://srtm.csi.cgiar.org/srtmdata/
  SRTM1 (30m): https://earthdata.nasa.gov/ (free account needed)
  OpenTopography: https://opentopography.org/

Tiles needed for geographic validation tests:
  V6  Den Helder Netherlands : N52E004
  V7  Dover England          : N51E001
  V8  Crete Greece           : N35E025
  V9  Lhasa Tibet            : N29E091
  V10 Al Jouf Saudi Arabia   : N29E040
  V11 Canvey Island UK       : N51E000

After placing tiles here, the app will auto-create *_filled.tif files
on first load. These are the void-filled versions — do not delete them.
```

---

## FINAL NOTES FOR CLAUDE CODE

- Void preprocessing is mandatory — treat any NaN or -32768 in a computation array as a fatal bug
- When in doubt about a formula, refer to the physics section of this file
- The BEL paper formulas are the ground truth for this project
- The Cambridge Pixel screenshot is the visual target for the GUI
- Performance matters: NumPy vectorisation everywhere, no Python loops over arrays
- This is a research/student project — code clarity and correctness matter more than micro-optimisation
- The app must launch, load a DEM, preprocess it, compute, and display coverage without any internet
- Test on Windows (primary target) and Linux
- When a module is complete, write its test file before moving to the next module
- Log void fill statistics every time a DEM is loaded — this data belongs in the GeoJSON export metadata