"""
Site Optimizer — Grid search for top-K radar sites by total coverage area.

Provides two pure-Python functions (no Qt):
- generate_grid_points: geodesic grid over a bounding box
- compute_coverage_score: replicate ComputationWorker radial math, return total km²

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
    """
    points = []
    lat = min_lat
    while lat <= max_lat + 1e-9:
        lon = min_lon
        while lon <= max_lon + 1e-9:
            points.append((lat, lon))
            # Step east: advance longitude by step_m at current lat
            lon_next, _, _ = GEOD.fwd(lon, lat, 90, step_m)
            lon = lon_next
        # Step north: advance latitude by step_m from the western edge
        _, lat_next, _ = GEOD.fwd(min_lon, lat, 0, step_m)
        lat = lat_next
    return points


def compute_coverage_score(request) -> float:
    """
    Run radial coverage computation for one site and return total coverage
    area (km²) summed across all height bands.

    Replicates the EXACT same radial math as ComputationWorker.run() in
    src/gui/main_window.py (lines 59–346), minus Qt signals, shadow
    extraction, and polar diagram data — those are not needed for scoring.

    Void fill is intentionally skipped (costs seconds per candidate site).
    The same inline nodata→0 cleanup that the worker does before the
    VOID_FILL_ENABLED check is applied here.

    Args:
        request: ComputationRequest dataclass from src/gui/control_panel.py.
                 Duck-typed so this module does not import the GUI package.

    Returns:
        Total coverage area in km² (float), summed over all height bands.

    Raises:
        ValueError: if no DEM path is set on the request.
        rasterio.errors.RasterioIOError: if the DEM file cannot be opened.
    """
    req = request

    if not req.dem_path:
        raise ValueError(
            "No DEM loaded. Set request.dem_path to a GeoTIFF file before scoring."
        )

    # ------------------------------------------------------------------
    # 1. Load DEM (float64, nodata → 0, unreasonable values → 0).
    #    Same logic as ComputationWorker.run() lines 79–87, without the
    #    optional VOID_FILL_ENABLED branch (too slow for grid search).
    # ------------------------------------------------------------------
    with rasterio.open(req.dem_path) as src:
        dem_data = src.read(1).astype(np.float64)
        transform = src.transform
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
        cols = (lons - transform.c) / transform.a
        rows = (lats - transform.f) / transform.e

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
    # 7. Build outer polygon ring and compute area for each height band.
    #    Mirrors worker lines 308–341 and area calculation described in
    #    the task specification.
    # ------------------------------------------------------------------
    total_area_km2 = 0.0

    for h in heights_agl:
        ranges_h = coverage_ranges_m[h]
        # Clamp degenerate near-zero radii to one range step so the polygon
        # doesn't collapse to a point (same guard as worker line 312)
        ranges_h = np.where(ranges_h < RANGE_STEP_M, RANGE_STEP_M, ranges_h)

        # Vectorised geodetic forward: all azimuths at once (lon first)
        lons_p, lats_p, _ = GEOD.fwd(
            np.full(n_az, ant_lon),
            np.full(n_az, ant_lat),
            azimuths,
            ranges_h
        )
        # Close the ring: first coord == last coord (GeoJSON polygon convention)
        lat_list = lats_p.tolist() + [lats_p[0]]
        lon_list = lons_p.tolist() + [lons_p[0]]
        coords = list(zip(lon_list, lat_list))  # shapely uses (lon, lat)

        poly = shapely.geometry.Polygon(coords)
        # geometry_area_perimeter returns signed area; abs() handles winding order
        area_m2, _ = GEOD.geometry_area_perimeter(poly)
        total_area_km2 += abs(area_m2) / 1e6

    return total_area_km2
