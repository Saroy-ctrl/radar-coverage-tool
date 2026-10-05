"""
Site Optimizer — Grid search for top-K radar sites by total coverage area.

Provides pure-Python functions (no Qt):
- generate_grid_points: geodesic grid over a bounding box
- compute_coverage_score: replicate ComputationWorker radial math, return total km²
- select_refine_pool: shortlist of best scout-scored candidates to re-score at full resolution
- sample_ground_elevation_m: DEM ground height at a single (lat, lon)
- land_candidates: drop sea / out-of-DEM candidates and attach ground elevation

Reference: ComputationWorker.run() in src/gui/main_window.py (lines 59–346).
No Qt imports anywhere in this module.
"""

import numpy as np
import rasterio
import shapely.geometry
from pyproj import Geod

from src.earth_model import EarthModel
from src.visibility_engine import VisibilityEngine
from src.obstruction_engine import ObstructionEngine

# Module-level geodetic object — same pattern as ComputationWorker.
# GEOD.fwd(lon, lat, az, dist) → (lon_new, lat_new, back_az)  — lon is FIRST.
GEOD = Geod(ellps='WGS84')

# Two-stage Top-K ranking. The coarse scout pass (500 m range step) skips terrain
# close to the antenna, which is what blocks low targets most, so its ranking is
# unreliable on its own (Tenerife test: scout top-5 contained 0 of the true top-5).
# Re-scoring a shortlist at full resolution recovered all 5 for ~8 s extra.
REFINE_POOL_MIN = 20     # candidates re-scored at full resolution, at least
REFINE_POOL_FACTOR = 4   # ... or this many per requested site, whichever is larger

# SRTM products store open water as exactly 0 m (and nodata is mapped to 0 on load),
# so a candidate pixel at exactly this value is sea, not a usable radar site.
SEA_SURFACE_M = 0.0


def generate_grid_points(
    min_lat: float, min_lon: float,
    max_lat: float, max_lon: float,
    step_m: float
) -> list[tuple[float, float]]:
    """
    Return (lat, lon) grid covering the bbox at step_m spacing.

    Steps along geodesic north/east from the SW corner using GEOD.fwd so
    the grid is correct on a spheroid (no flat-earth approximation).

    Args:
        min_lat: southern boundary (degrees)
        min_lon: western boundary (degrees)
        max_lat: northern boundary (degrees)
        max_lon: eastern boundary (degrees)
        step_m:  grid spacing in metres (both north and east)

    Returns:
        List of (lat, lon) tuples covering the bounding box.

    Note:
        GEOD.fwd(lon, lat, az, dist) — lon FIRST (pyproj convention).
        Azimuth 0 = north, 90 = east.
        Row length varies with latitude (geodesic east step contracts near poles).
    """
    if step_m <= 0:
        raise ValueError(f"step_m must be positive, got {step_m}")

    points = []
    lat = min_lat
    while lat <= max_lat + 1e-9:
        lon = min_lon
        while lon <= max_lon + 1e-9:
            points.append((lat, lon))
            # Step east: advance longitude by step_m at current lat
            lon_next, _, _ = GEOD.fwd(lon, lat, 90, step_m)
            if lon_next <= lon + 1e-10:
                # Near poles, easting produces negligible lon change — stop row
                break
            lon = lon_next
        # Step north: advance latitude by step_m from the western edge
        _, lat_next, _ = GEOD.fwd(min_lon, lat, 0, step_m)
        if lat_next <= lat + 1e-10:
            # Degenerate — no northward progress (should not happen in normal use)
            break
        lat = lat_next
    return points


def _ray_bbox_distances(
    ant_lat_deg: float,
    ant_lon_deg: float,
    azimuths_deg: np.ndarray,
    bbox: tuple,
) -> np.ndarray:
    """
    Flat-earth distance (metres) from a point inside bbox to the bbox boundary
    along each azimuth.

    Uses a local Cartesian frame with the bbox SW corner as origin, axes in
    metres (north = +y, east = +x). Accuracy ~0.3% for bbox sizes up to 300 km,
    which is sufficient for a ranking metric.

    Args:
        ant_lat_deg: candidate latitude (degrees)
        ant_lon_deg: candidate longitude (degrees)
        azimuths_deg: azimuths in degrees (clockwise from north)
        bbox: (min_lat, min_lon, max_lat, max_lon)

    Returns:
        Array of distances in metres, one per azimuth. Always > 0 for a
        point strictly inside the bbox.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    lat_rad = np.radians(ant_lat_deg)

    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * np.cos(lat_rad)

    y = (ant_lat_deg - min_lat) * m_per_deg_lat   # metres north of south edge
    x = (ant_lon_deg - min_lon) * m_per_deg_lon   # metres east of west edge

    H = (max_lat - min_lat) * m_per_deg_lat        # total bbox height (m)
    W = (max_lon - min_lon) * m_per_deg_lon        # total bbox width (m)

    az_rad = np.radians(np.asarray(azimuths_deg, dtype=np.float64))
    dy = np.cos(az_rad)   # north component of unit direction
    dx = np.sin(az_rad)   # east component of unit direction

    INF = np.full_like(az_rad, np.inf)

    # Parametric t to hit each edge; t has units of metres (unit direction vector).
    # errstate suppresses divide-by-zero: np.where evaluates both branches before
    # selecting, so masked zero-dy/dx values still produce a division attempt.
    with np.errstate(divide='ignore', invalid='ignore'):
        t_N = np.where(dy >  1e-12, (H - y) / dy, INF)
        t_S = np.where(dy < -1e-12,      -y / dy, INF)
        t_E = np.where(dx >  1e-12, (W - x) / dx, INF)
        t_W = np.where(dx < -1e-12,      -x / dx, INF)

    return np.minimum(np.minimum(t_N, t_S), np.minimum(t_E, t_W))


def compute_coverage_score(request, *, dem_data: np.ndarray = None, dem_transform=None,
                           bbox: tuple = None) -> float:
    """
    Run radial coverage computation for one site and return total capped-radial
    coverage area (km²) summed across all height bands.

    When bbox=(min_lat, min_lon, max_lat, max_lon) is provided each azimuth's
    coverage range is capped at the distance to the bbox boundary in that
    direction before the scoring polygon is built.  This means sites that
    overshoot the bbox edge in one direction receive no extra credit — the
    metric rewards sites whose coverage reaches ALL bbox edges, not just sites
    with one very long unidirectional arm (which the raw polygon-area metric
    favours due to its quadratic dependence on range).

    Replicates the EXACT same radial math as ComputationWorker.run() in
    src/gui/main_window.py (lines 59–346), minus Qt signals, shadow
    extraction, and polar diagram data — those are not needed for scoring.

    Void fill is intentionally skipped (costs seconds per candidate site).
    The same inline nodata→0 cleanup that the worker does before the
    VOID_FILL_ENABLED check is applied here.

    Args:
        request: ComputationRequest dataclass from src/gui/control_panel.py.
                 Duck-typed so this module does not import the GUI package.
        dem_data: Optional pre-loaded DEM array (float64). When provided
                  together with dem_transform, skips file I/O entirely —
                  allows callers to open the GeoTIFF once and reuse it
                  across 900+ candidate sites.
        dem_transform: Optional rasterio Affine transform matching dem_data.
                       Must be supplied together with dem_data or neither.

    Returns:
        Total coverage area in km² (float), summed over all height bands.

    Raises:
        ValueError: if no DEM path is set on the request (fallback path only).
        rasterio.errors.RasterioIOError: if the DEM file cannot be opened.
    """
    req = request

    nodata = None
    if dem_data is None or dem_transform is None:
        # Fallback: load from disk (standalone calls and tests)
        if not req.dem_path:
            raise ValueError(
                "No DEM loaded. Set request.dem_path to a GeoTIFF file before scoring."
            )
        with rasterio.open(req.dem_path) as src:
            dem_data = src.read(1).astype(np.float64)
            dem_transform = src.transform
            nodata = src.nodata
        if nodata is not None:
            dem_data = np.where(dem_data == nodata, 0.0, dem_data)
        dem_data = np.where((dem_data < -500) | (dem_data > 9000), 0.0, dem_data)

    # ------------------------------------------------------------------
    # 2. Vectorised DEM sampler closure — identical to worker lines 109–126.
    #    Out-of-bounds points return 0.0 (sea level), not a boundary pixel.
    # ------------------------------------------------------------------
    def sample_dem(lats, lons):
        # rasterio affine: col = (lon - x_origin) / pixel_width
        #                   row = (lat - y_origin) / pixel_height  (pixel_height < 0)
        cols = (lons - dem_transform.c) / dem_transform.a
        rows = (lats - dem_transform.f) / dem_transform.e

        in_bounds = (
            (rows >= 0) & (rows < dem_data.shape[0]) &
            (cols >= 0) & (cols < dem_data.shape[1])
        )

        rows_c = np.clip(np.round(rows).astype(int), 0, dem_data.shape[0] - 1)
        cols_c = np.clip(np.round(cols).astype(int), 0, dem_data.shape[1] - 1)

        result = dem_data[rows_c, cols_c]
        # Outside DEM: return 0 (sea level) rather than a clipped boundary value
        result = np.where(in_bounds, result, 0.0)
        return result

    # ------------------------------------------------------------------
    # 3. Physics engines — identical to worker lines 129–133.
    # ------------------------------------------------------------------
    earth = EarthModel(k_factor=req.k_factor)
    vis = VisibilityEngine(
        earth_model_instance=earth,
        diffraction_guard_rad=np.radians(req.diffraction_guard_deg)
    )

    # ------------------------------------------------------------------
    # 4. Obstructions — identical to worker lines 136–143.
    # ------------------------------------------------------------------
    obs_engine = ObstructionEngine()
    obstructions_df = None
    if req.obstructions_path:
        try:
            obstructions_df = obs_engine.load_obstructions(req.obstructions_path)
        except Exception:
            pass  # Missing/malformed obstruction file: skip silently

    # ------------------------------------------------------------------
    # 5. Derived scalars — same as worker lines 145–161.
    # ------------------------------------------------------------------
    ant_lat = req.radar_lat
    ant_lon = req.radar_lon
    antenna_amsl_m = req.antenna_amsl_m
    site_elev_m = req.site_elevation_amsl_m
    max_range_m = req.max_range_km * 1000.0

    RANGE_STEP_M = req.range_step_m      # hardcoded Ultra (50 m) via dataclass default
    AZIMUTH_STEP = req.azimuth_step_deg  # hardcoded Ultra (0.5°) via dataclass default

    azimuths = np.arange(0, 360, AZIMUTH_STEP, dtype=np.float64)
    n_az = len(azimuths)

    # Profile ranges: 0 (antenna bin) then every RANGE_STEP_M out to max_range_m.
    # CRITICAL: must start at 0 so bin 0 is the antenna position itself.
    # See CLAUDE.md comment about why starting at RANGE_STEP_M breaks coverage.
    ranges = np.arange(0.0, max_range_m + RANGE_STEP_M, RANGE_STEP_M,
                       dtype=np.float64)

    heights_agl = req.height_bands_m

    min_beam_rad = np.radians(req.min_beam_deg)
    max_beam_rad = np.radians(req.max_beam_deg)

    # Per-azimuth outer coverage ranges for each height band (metres)
    coverage_ranges_m = {h: np.zeros(n_az, dtype=np.float64) for h in heights_agl}

    # ------------------------------------------------------------------
    # 6. Main azimuth loop — identical to worker lines 186–275 minus
    #    shadow extraction and progress signals.
    # ------------------------------------------------------------------
    for i, az in enumerate(azimuths):
        # Forward geodetic: all range bins at once (lon first — pyproj convention)
        lons_p, lats_p, _ = GEOD.fwd(
            np.full(len(ranges), ant_lon),
            np.full(len(ranges), ant_lat),
            np.full(len(ranges), az),
            ranges
        )
        # Antenna bin (range=0): fix to exact antenna position
        lats_p[0] = ant_lat
        lons_p[0] = ant_lon

        elevations = sample_dem(np.asarray(lats_p), np.asarray(lons_p))
        # Antenna bin: use the known site elevation, not a DEM sample
        elevations[0] = site_elev_m

        profile = {
            'range_m': ranges,
            'elevation_amsl_m': elevations
        }

        # Inject obstructions for this azimuth
        if obstructions_df is not None:
            try:
                profile = obs_engine.inject_obstructions(
                    profile, ant_lat, ant_lon, obstructions_df=obstructions_df
                )
            except Exception:
                pass

        # Horizon computation (once per azimuth, reused for all heights)
        horizon_result = vis.compute_horizon_angles(profile, antenna_amsl_m)
        horizon_angles = horizon_result['horizon_angle_rad']
        h_apparent     = horizon_result['h_apparent_m']   # shape: (n_ranges,)

        # Per-height AGL coverage
        # target_angle[j] = arctan2(h_apparent[j] + h_agl - antenna_amsl, ranges[j])
        # Coverage ends where terrain horizon angle exceeds decreasing target angle.
        for h_agl in heights_agl:
            # Per-range target elevation angles
            target_angles = np.arctan2(
                h_apparent + h_agl - antenna_amsl_m,
                ranges
            )

            # Gate 1: terrain visibility (cumulative horizon)
            terrain_visible = target_angles[1:] >= horizon_angles[1:]
            # Gate 2: beam elevation window (transparent at ±90° defaults)
            beam_within = (
                (target_angles[1:] >= min_beam_rad) &
                (target_angles[1:] <= max_beam_rad)
            )
            combined_mask = terrain_visible & beam_within
            visible_idx   = np.where(combined_mask)[0]

            # Outer boundary: last combined-visible bin
            if len(visible_idx) > 0:
                max_r = float(ranges[1:][visible_idx[-1]])
            else:
                max_r = float(ranges[1])

            coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)

    # ------------------------------------------------------------------
    # 7. Build scoring polygon and compute area for each height band.
    #
    #    When bbox is provided, each azimuth's range is capped at the distance
    #    to the bbox edge in that direction (via _ray_bbox_distances).  The
    #    polygon is then built from these capped ranges so coverage that
    #    extends beyond the bbox boundary contributes nothing to the score.
    #
    #    Effect on site ranking: the old area metric was quadratic in range
    #    (area ∝ r²), so a site with one 100 km arm scored 100× a site with
    #    one 10 km arm.  After capping, once a direction's coverage reaches
    #    the bbox edge, the contribution in that direction is bounded — the
    #    metric now rewards sites that reach the bbox boundary in ALL
    #    directions rather than sites with a single long open arm.
    # ------------------------------------------------------------------
    if bbox is not None:
        d_bbox = _ray_bbox_distances(ant_lat, ant_lon, azimuths, bbox)
    else:
        d_bbox = None

    # Score by the minimum height band only — the binding constraint.
    # Coverage at a lower AGL target is always harder to achieve than at a
    # higher target (terrain occults more), so maximising bbox coverage at
    # min_h implicitly maximises it across the entire user-specified range.
    # Using the max-height polygon (or a union) would be dominated by the
    # easiest-to-achieve band and collapse all high-elevation sites to the
    # same score.
    score_h = min(heights_agl)
    ranges_h = coverage_ranges_m[score_h].copy()

    # Clamp degenerate near-zero radii
    ranges_h = np.where(ranges_h < RANGE_STEP_M, RANGE_STEP_M, ranges_h)

    if d_bbox is not None:
        ranges_h = np.minimum(ranges_h, d_bbox)
        ranges_h = np.where(ranges_h < RANGE_STEP_M, RANGE_STEP_M, ranges_h)

    lons_p, lats_p, _ = GEOD.fwd(
        np.full(n_az, ant_lon),
        np.full(n_az, ant_lat),
        azimuths,
        ranges_h,
    )
    lat_list = lats_p.tolist() + [lats_p[0]]
    lon_list = lons_p.tolist() + [lons_p[0]]
    coords = list(zip(lon_list, lat_list))

    poly = shapely.geometry.Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return 0.0

    area_m2, _ = GEOD.geometry_area_perimeter(poly)
    return abs(area_m2) / 1e6


def refine_pool_size(k: int) -> int:
    """Number of scout-ranked candidates to re-score at full resolution for top-k."""
    return max(REFINE_POOL_MIN, REFINE_POOL_FACTOR * k)


def select_refine_pool(scored: list, k: int) -> list:
    """
    Shortlist the best scout-scored candidates for the full-resolution pass.

    Args:
        scored: [(lat, lon, score_km2, elev_amsl_m), ...] from the scout pass
        k: number of sites the user asked for

    Returns:
        Up to refine_pool_size(k) entries, highest score first.
    """
    return sorted(scored, key=lambda s: -s[2])[: refine_pool_size(k)]


def sample_ground_elevation_m(dem_path: str, lat: float, lon: float):
    """
    Ground height (m AMSL) of the DEM pixel containing (lat, lon).

    Returns the pixel that contains the point (floor of the corner-based pixel
    index) and uses the same nodata/out-of-range → 0 m (sea level) convention
    as ComputationWorker.

    Returns:
        float elevation in metres, or None if (lat, lon) is outside the DEM.
    """
    with rasterio.open(dem_path) as src:
        transform = src.transform
        col = (lon - transform.c) / transform.a
        row = (lat - transform.f) / transform.e
        if not (0 <= row < src.height and 0 <= col < src.width):
            return None
        r, c = int(row), int(col)   # non-negative here, so int() == floor
        value = float(src.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])
        nodata = src.nodata
    if (nodata is not None and value == nodata) or value < -500 or value > 9000:
        return 0.0
    return value


def land_candidates(points: list, dem_data: np.ndarray, dem_transform) -> tuple:
    """
    Keep only candidate sites that sit on land inside the DEM.

    Ground elevation uses the same nearest-pixel rule as the coverage
    computation (round, clamped to the array). After nodata→0 cleanup, open
    water in SRTM products is exactly SEA_SURFACE_M, so those pixels are sea.
    Land below sea level (negative values, e.g. polders) is kept.

    Args:
        points: [(lat, lon), ...] candidate sites
        dem_data: 2D elevation array (m AMSL), nodata already set to 0
        dem_transform: rasterio affine transform of dem_data

    Returns:
        (land, n_sea, n_outside) where land = [(lat, lon, elev_amsl_m), ...]
    """
    n_rows, n_cols = dem_data.shape
    land, n_sea, n_outside = [], 0, 0
    for lat, lon in points:
        col = (lon - dem_transform.c) / dem_transform.a
        row = (lat - dem_transform.f) / dem_transform.e
        if not (0 <= row < n_rows and 0 <= col < n_cols):
            n_outside += 1
            continue
        r = min(int(round(row)), n_rows - 1)
        c = min(int(round(col)), n_cols - 1)
        elev_m = float(dem_data[r, c])
        if elev_m == SEA_SURFACE_M:
            n_sea += 1
            continue
        land.append((lat, lon, elev_m))
    return land, n_sea, n_outside
