"""
Obstruction Engine — Load and inject CSV obstacles into elevation profiles.

Obstacles (towers, buildings, mountains) are stored in CSV format:
  lat, lon, height_amsl_m, type, description

For each obstacle, the engine:
1. Computes range and bearing from antenna
2. Finds the nearest range bin in the elevation profile
3. Updates height_amsl_m at that bin (take max of terrain + obstacle)

All coordinates in WGS84. All heights in SI units (metres AMSL).

Reference: pyproj Geod for distance/bearing computation.
"""

import numpy as np
import pandas as pd
import logging
from pathlib import Path
from pyproj import Geod

logger = logging.getLogger(__name__)

GEOD = Geod(ellps='WGS84')


class ObstructionEngine:
    """Load and inject obstacles into radial elevation profiles."""

    def __init__(self, obstruction_dir=None):
        """
        Initialize Obstruction Engine.

        Args:
            obstruction_dir (str or Path): directory containing CSV obstacle files.
                If None, uses 'data/obstructions/' relative to cwd.
        """
        if obstruction_dir is None:
            obstruction_dir = Path.cwd() / 'data' / 'obstructions'
        self.obstruction_dir = Path(obstruction_dir)
        self.obstruction_dir.mkdir(parents=True, exist_ok=True)

    def load_obstructions(self, csv_path):
        """
        Load obstructions from CSV file.

        Expected columns: lat, lon, height_amsl_m, type, description

        Args:
            csv_path (str or Path): path to CSV file

        Returns:
            pd.DataFrame: obstacle data with columns as above

        Raises:
            FileNotFoundError: if CSV not found
            ValueError: if required columns missing
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise FileNotFoundError(f"Obstruction CSV not found: {csv_path}")

        df = pd.read_csv(csv_path)
        required_cols = {'lat', 'lon', 'height_amsl_m', 'type', 'description'}
        if not required_cols.issubset(df.columns):
            raise ValueError(
                f"CSV missing required columns. Expected {required_cols}, got {set(df.columns)}"
            )

        logger.info(f"Loaded {len(df)} obstructions from {csv_path}")
        return df

    def inject_obstructions(self, profile_dict, antenna_lat_deg, antenna_lon_deg,
                            obstructions_df=None, csv_path=None):
        """
        Inject obstacles into radial elevation profile.

        For each obstacle:
        1. Compute range and bearing from antenna
        2. Match to nearest range bin in profile
        3. Update elevation: h_amsl[i] = max(h_terrain[i], h_obstacle)

        Args:
            profile_dict (dict): output from terrain_engine.extract_radial_profile()
                with keys: range_m, elevation_amsl_m, lat_points, lon_points
            antenna_lat_deg (float): antenna latitude (degrees)
            antenna_lon_deg (float): antenna longitude (degrees)
            obstructions_df (pd.DataFrame, optional): obstacle dataframe
            csv_path (str or Path, optional): path to CSV file (if obstructions_df is None)

        Returns:
            dict: modified profile_dict with updated elevation_amsl_m

        Example:
            profile = terrain_engine.extract_radial_profile(...)
            modified = obstruction_engine.inject_obstructions(
                profile, antenna_lat, antenna_lon, csv_path='data/obstructions/site.csv'
            )
        """
        if obstructions_df is None and csv_path is not None:
            obstructions_df = self.load_obstructions(csv_path)
        elif obstructions_df is None:
            logger.warning("No obstructions provided")
            return profile_dict

        result = profile_dict.copy()
        elevation = result['elevation_amsl_m'].copy()
        ranges = result['range_m']

        for idx, obs in obstructions_df.iterrows():
            obs_lat = obs['lat']
            obs_lon = obs['lon']
            obs_height = obs['height_amsl_m']
            obs_type = obs['type']
            obs_desc = obs['description']

            # Compute distance and bearing to obstacle
            _, _, distance_m = GEOD.inv(
                antenna_lon_deg, antenna_lat_deg,
                obs_lon, obs_lat
            )

            # Find nearest range bin
            nearest_idx = np.argmin(np.abs(ranges - distance_m))
            nearest_range = ranges[nearest_idx]

            # Inject: take max of terrain and obstacle
            elevation[nearest_idx] = max(elevation[nearest_idx], obs_height)

            logger.debug(
                f"Injected obstacle '{obs_desc}' ({obs_type}) at range {nearest_range/1000:.2f} km, "
                f"height {obs_height:.1f} m"
            )

        result['elevation_amsl_m'] = elevation
        return result
