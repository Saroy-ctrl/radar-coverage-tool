"""
Terrain Engine — DEM loading, stitching, and radial extraction.

Responsibilities:
1. Load SRTM3 GeoTIFF tiles using rasterio
2. Call dem_preprocessor if no *_filled.tif cache exists
3. Stitch adjacent tiles seamlessly (for large coverage areas)
4. Extract radial profiles: given (lat, lon, azimuth, max_range_m) →
   return (range_m array, elevation_amsl_m array)

All coordinates in WGS84 (EPSG:4326). All distances/heights in SI units (metres).

Reference: rasterio documentation, SRTM3 tile layout, pyproj Geod.
"""

import numpy as np
import logging
from pathlib import Path
import rasterio
from rasterio.transform import Affine
from pyproj import Geod
from . import dem_preprocessor

logger = logging.getLogger(__name__)

GEOD = Geod(ellps='WGS84')  # Geodetic calculations (Haversine distance/bearing)
FILLED_SUFFIX = '_filled.tif'  # Cache file suffix


class TerrainEngine:
    """
    DEM loader and radial profile extractor.

    Loads SRTM3 GeoTIFF tiles, applies void filling on first load,
    caches as *_filled.tif, and extracts elevation profiles along radials.
    """

    def __init__(self, dem_dir=None):
        """
        Initialize Terrain Engine.

        Args:
            dem_dir (str or Path): directory containing SRTM GeoTIFF tiles.
                If None, uses 'data/dem/' relative to cwd.
        """
        if dem_dir is None:
            dem_dir = Path.cwd() / 'data' / 'dem'
        self.dem_dir = Path(dem_dir)
        self.dem_dir.mkdir(parents=True, exist_ok=True)
        self._tile_cache = {}  # {(lat_tile, lon_tile): (data, transform, crs)}

    def _get_tile_key(self, lat_deg, lon_deg):
        """
        Compute SRTM3 tile key from lat/lon.

        SRTM3 tiles are 1° x 1° and named NxxWyy or SxxEyy where
        xx = latitude (00–90), yy = longitude (000–180).

        Args:
            lat_deg (float): latitude in degrees (WGS84)
            lon_deg (float): longitude in degrees (WGS84)

        Returns:
            str: tile name (e.g. "N51E001", "S34W056")
        """
        lat_int = int(np.floor(lat_deg))
        lon_int = int(np.floor(lon_deg))

        lat_str = f"N{abs(lat_int):02d}" if lat_int >= 0 else f"S{abs(lat_int):02d}"
        lon_str = f"E{abs(lon_int):03d}" if lon_int >= 0 else f"W{abs(lon_int):03d}"

        return f"{lat_str}{lon_str}"

    def load_tile(self, tile_name):
        """
        Load a single DEM tile (GeoTIFF).

        Workflow:
        1. Check if tile_name_filled.tif exists → use it
        2. If not, load tile_name.tif → apply preprocessor → save as _filled.tif
        3. Cache in memory

        Args:
            tile_name (str): SRTM tile name (e.g. "N51E001")

        Returns:
            tuple: (elevation_array, transform, crs)
                - elevation_array: 2D float64 array (no NaNs)
                - transform: rasterio Affine transform
                - crs: coordinate reference system

        Raises:
            FileNotFoundError: if tile_name.tif not found
            ValueError: if void fill fails assertions
        """
        if tile_name in self._tile_cache:
            return self._tile_cache[tile_name]

        # Try filled tile first
        filled_path = self.dem_dir / f"{tile_name}{FILLED_SUFFIX}"
        if filled_path.exists():
            logger.info(f"Loading cached filled tile: {filled_path}")
            with rasterio.open(filled_path) as src:
                elevation = src.read(1).astype(np.float64)
                transform = src.transform
                crs = src.crs
            self._tile_cache[tile_name] = (elevation, transform, crs)
            return elevation, transform, crs

        # Load raw tile
        raw_path = self.dem_dir / f"{tile_name}.tif"
        if not raw_path.exists():
            raise FileNotFoundError(f"DEM tile not found: {raw_path}")

        logger.info(f"Loading raw tile: {raw_path}")
        with rasterio.open(raw_path) as src:
            elevation = src.read(1).astype(np.float64)
            transform = src.transform
            crs = src.crs
            nodata = src.nodata

        # Apply preprocessing
        logger.info(f"Preprocessing tile {tile_name} for voids...")
        result = dem_preprocessor.fill_voids(elevation)
        elevation_filled = result['filled']
        void_frac = result['void_fraction']

        logger.info(
            f"Tile {tile_name}: {result['void_count']} voids "
            f"({void_frac*100:.2f}%) filled"
        )

        # Cache as GeoTIFF
        logger.info(f"Caching filled tile: {filled_path}")
        with rasterio.open(
            filled_path, 'w',
            driver='GTiff',
            height=elevation_filled.shape[0],
            width=elevation_filled.shape[1],
            count=1,
            dtype=np.float32,
            crs=crs,
            transform=transform,
            compress='lzw',
            nodata=-9999
        ) as dst:
            dst.write(elevation_filled.astype(np.float32), 1)

        self._tile_cache[tile_name] = (elevation_filled, transform, crs)
        return elevation_filled, transform, crs

    def _sample_elevation(self, lat_deg, lon_deg, tile_data):
        """
        Sample elevation at (lat, lon) from loaded tile.

        Uses bilinear interpolation (rasterio window reads).

        Args:
            lat_deg (float): latitude (degrees, WGS84)
            lon_deg (float): longitude (degrees, WGS84)
            tile_data (tuple): (elevation_array, transform, crs)

        Returns:
            float: elevation in metres AMSL
        """
        elevation, transform, _ = tile_data

        # Convert lat/lon to pixel coordinates
        # Transform maps (row, col) → (x, y) in geographic coords
        # We invert to get (x, y) → (row, col)
        inv_transform = ~transform
        col, row = inv_transform * (lon_deg, lat_deg)

        row, col = int(np.round(row)), int(np.round(col))

        # Clamp to array bounds
        row = np.clip(row, 0, elevation.shape[0] - 1)
        col = np.clip(col, 0, elevation.shape[1] - 1)

        return elevation[row, col]

    def extract_radial_profile(self, lat_antenna_deg, lon_antenna_deg,
                               azimuth_deg, max_range_m,
                               range_resolution_m=100):
        """
        Extract elevation profile along a radial from antenna.

        Approach:
        1. Starting from antenna position, walk outward along azimuth
        2. Every range_resolution_m, sample terrain elevation
        3. Return arrays: (range_m, elevation_amsl_m)

        Args:
            lat_antenna_deg (float): antenna latitude (degrees, WGS84)
            lon_antenna_deg (float): antenna longitude (degrees, WGS84)
            azimuth_deg (float): direction in degrees (0°=North, 90°=East, compass convention)
            max_range_m (float): maximum radial distance in metres
            range_resolution_m (float): sampling interval in metres (default 100 m)

        Returns:
            dict: {
                'range_m': 1D array of ranges (metres)
                'elevation_amsl_m': 1D array of elevations (metres AMSL)
                'lat_points': 1D array of sampled latitudes (degrees)
                'lon_points': 1D array of sampled longitudes (degrees)
            }

        Assertions:
            - No NaN elevations in output
        """
        # Generate range bins
        range_bins = np.arange(0, max_range_m + range_resolution_m, range_resolution_m)
        n_bins = len(range_bins)

        # Storage
        elevations = np.full(n_bins, np.nan, dtype=np.float64)
        lats = np.full(n_bins, np.nan, dtype=np.float64)
        lons = np.full(n_bins, np.nan, dtype=np.float64)

        # Convert azimuth to compass bearing for pyproj
        # (azimuth_deg: 0°=N, 90°=E is compass convention used by Geod)
        azimuth_rad = np.radians(azimuth_deg)

        for i, range_m in enumerate(range_bins):
            if range_m == 0:
                # Antenna position
                lats[i] = lat_antenna_deg
                lons[i] = lon_antenna_deg
                tile_key = self._get_tile_key(lat_antenna_deg, lon_antenna_deg)
                tile_data = self.load_tile(tile_key)
                elevations[i] = self._sample_elevation(
                    lat_antenna_deg, lon_antenna_deg, tile_data
                )
            else:
                # Endpoint of great-circle arc at azimuth, distance range_m
                lon_p, lat_p, _ = GEOD.fwd(
                    lon_antenna_deg, lat_antenna_deg,
                    azimuth_deg, range_m
                )
                lats[i] = lat_p
                lons[i] = lon_p

                # Load appropriate tile and sample
                tile_key = self._get_tile_key(lat_p, lon_p)
                tile_data = self.load_tile(tile_key)
                elevations[i] = self._sample_elevation(lat_p, lon_p, tile_data)

        # Assertion: no NaNs
        assert np.isnan(elevations).sum() == 0, \
            f"NaN elevations in radial profile (count={np.isnan(elevations).sum()})"

        return {
            'range_m': range_bins,
            'elevation_amsl_m': elevations,
            'lat_points': lats,
            'lon_points': lons
        }
