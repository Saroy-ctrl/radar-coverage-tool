# src/dem_manager.py
"""
DEM tile manager — SRTM3 tile name convention + rasterio multi-tile mosaic.

Responsibility:
  open_mosaic(dem_path, radar_lat, radar_lon, max_range_km)
    → (dem_array_float64, transform, bounds_namedtuple, n_tiles_used)

If only one tile covers the bounding box, it is returned directly.
If multiple tiles exist in the same directory, rasterio.merge stitches them.
If neighbor tiles are not found, the single specified file is used and a
warning is logged (cross-tile radials fall back to sea level, same as before).
"""

import logging
import numpy as np
from pathlib import Path

import rasterio
from rasterio.merge import merge as rio_merge
from rasterio.coords import BoundingBox
from rasterio.transform import array_bounds

logger = logging.getLogger(__name__)


def _srtm_tile_name(lat_floor: int, lon_floor: int) -> str:
    """
    Convert integer tile-corner coordinates to SRTM3 filename.

    E.g.: lat_floor=28, lon_floor=-17 → "N28W017.tif"
          lat_floor=-1, lon_floor=36   → "S01E036.tif"
    """
    ns = 'N' if lat_floor >= 0 else 'S'
    ew = 'E' if lon_floor >= 0 else 'W'
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}.tif"


def find_required_tiles(radar_lat: float, radar_lon: float, max_range_km: float) -> list[str]:
    """
    Return SRTM3 filenames for all 1°×1° tiles that intersect the bounding box
    defined by (radar_lat, radar_lon) ± max_range_km (plus 1° safety margin).

    Args:
        radar_lat, radar_lon: antenna WGS84 decimal degrees
        max_range_km:         maximum instrumented range in kilometres

    Returns:
        list[str]: SRTM3 filenames, e.g. ["N28W017.tif", "N28W016.tif", ...]
    """
    margin_deg = max_range_km / 111.0 + 1.0   # 111 km ≈ 1° latitude
    lat_min = int(np.floor(radar_lat - margin_deg))
    lat_max = int(np.floor(radar_lat + margin_deg))
    lon_min = int(np.floor(radar_lon - margin_deg))
    lon_max = int(np.floor(radar_lon + margin_deg))

    return [
        _srtm_tile_name(lat, lon)
        for lat in range(lat_min, lat_max + 1)
        for lon in range(lon_min, lon_max + 1)
    ]


def open_mosaic(
    dem_path: str,
    radar_lat: float,
    radar_lon: float,
    max_range_km: float,
) -> tuple:
    """
    Open DEM as a mosaic of all available SRTM tiles covering the bounding box.

    Searches the directory containing `dem_path` for any neighbour tiles
    matching the SRTM3 filename convention.  If none are found beside the
    explicitly selected file, that single file is used without mosaicking
    (same behaviour as before this function existed).

    Args:
        dem_path:      Path to the primary DEM GeoTIFF (user-selected via Browse DEM)
        radar_lat:     Antenna WGS84 latitude
        radar_lon:     Antenna WGS84 longitude
        max_range_km:  Maximum instrumented range (km) — defines bounding box

    Returns:
        (dem_array, transform, bounds, n_tiles)
        dem_array: float64 ndarray, raw elevations (nodata not yet filled)
        transform: rasterio Affine transform
        bounds:    rasterio BoundingBox namedtuple (left, bottom, right, top)
        n_tiles:   number of tiles merged (1 = single file, no mosaic)
    """
    dem_file = Path(dem_path)
    dem_dir  = dem_file.parent

    required = find_required_tiles(radar_lat, radar_lon, max_range_km)
    neighbours = [
        dem_dir / name
        for name in required
        if (dem_dir / name).exists() and (dem_dir / name) != dem_file
    ]

    if not neighbours:
        # Single-tile path (original behaviour)
        with rasterio.open(dem_file) as src:
            data = src.read(1).astype(np.float64)
            transform = src.transform
            bounds = src.bounds
        logger.info(f"DEM: single tile {dem_file.name}  ({len(required)} tiles required)")
        if len(required) > 1:
            logger.warning(
                f"{len(required) - 1} required tile(s) not found in {dem_dir} — "
                f"cross-tile radials will fall back to sea level (0 m)."
            )
        return data, transform, bounds, 1

    # Multi-tile mosaic
    all_paths = [dem_file] + neighbours
    logger.info(f"DEM mosaic: merging {len(all_paths)} tiles: {[p.name for p in all_paths]}")

    datasets = [rasterio.open(p) for p in all_paths]
    try:
        merged_array, merged_transform = rio_merge(
            datasets, nodata=-32768.0, dtype='float64'
        )
    finally:
        for ds in datasets:
            ds.close()

    dem_array = merged_array[0]   # single band
    h, w = dem_array.shape
    left, bottom, right, top = array_bounds(h, w, merged_transform)
    bounds = BoundingBox(left, bottom, right, top)

    return dem_array, merged_transform, bounds, len(all_paths)
