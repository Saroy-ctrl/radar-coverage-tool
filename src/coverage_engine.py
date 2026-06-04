"""
Coverage Engine — Polar to GeoJSON export and coverage area computation.

Responsibilities:
1. Accept arrays of azimuths and max_ranges (single height)
2. Output GeoJSON Polygon (closed ring, first coord == last)
3. Compute coverage area in km²
4. Support PNG/GeoTIFF/GeoJSON export with height band colours

Reference: GeoJSON spec RFC 7946, pyproj Geod for area computation.
"""

import numpy as np
import json
import logging
from pathlib import Path
from pyproj import Geod

logger = logging.getLogger(__name__)

GEOD = Geod(ellps='WGS84')

# Height band colours (Cambridge Pixel convention)
# Stored as height (m) → {color: hex, opacity: float}
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},
    100:  {"color": "#aacc00", "opacity": 0.40},
    500:  {"color": "#ff8800", "opacity": 0.40},
    1000: {"color": "#ff3300", "opacity": 0.35},
    3000: {"color": "#cc00ff", "opacity": 0.30},
}


class CoverageEngine:
    """Generate GeoJSON coverage polygons and compute coverage areas."""

    def __init__(self):
        """Initialize Coverage Engine."""
        pass

    def polar_to_geojson(self, antenna_lat_deg, antenna_lon_deg,
                         azimuths_deg, max_ranges_m, height_m=None):
        """
        Convert polar coverage (azimuth/range pairs) to GeoJSON Polygon.

        Algorithm:
        1. For each (azimuth, range) pair, compute (lat, lon) endpoint
        2. Collect all endpoints in order
        3. Close ring: repeat first coordinate at end
        4. Return GeoJSON FeatureCollection with Polygon geometry

        Args:
            antenna_lat_deg (float): antenna latitude (degrees, WGS84)
            antenna_lon_deg (float): antenna longitude (degrees, WGS84)
            azimuths_deg (np.ndarray): array of azimuths (degrees, compass: 0°=N, 90°=E)
            max_ranges_m (np.ndarray): array of max ranges (metres), same length as azimuths_deg
            height_m (float, optional): height band for coloring (if provided)

        Returns:
            dict: GeoJSON FeatureCollection with single Polygon feature
                {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[lon, lat], ...]] (closed ring)
                        },
                        "properties": {
                            "antenna_lat": float,
                            "antenna_lon": float,
                            "height_m": float,
                            "coverage_area_km2": float,
                            "color": hex string,
                            "opacity": float
                        }
                    }]
                }

        Physical meaning:
        - Polygon boundary = coverage contour at specified height
        - Interior of polygon = area within range (visible to antenna)
        - Antenna point at (antenna_lat, antenna_lon) typically INSIDE polygon
        """
        azimuths_deg = np.asarray(azimuths_deg, dtype=np.float64)
        max_ranges_m = np.asarray(max_ranges_m, dtype=np.float64)

        # Compute endpoints via forward geodetic calculation
        coordinates = []
        for az_deg, range_m in zip(azimuths_deg, max_ranges_m):
            lon, lat, _ = GEOD.fwd(
                antenna_lon_deg, antenna_lat_deg,
                az_deg, range_m
            )
            coordinates.append([lon, lat])

        # Close the ring
        if len(coordinates) > 0:
            coordinates.append(coordinates[0])

        # Compute coverage area
        coverage_area_km2 = self.compute_polygon_area(coordinates[:-1])  # Exclude repeated end point

        # Get color for height band
        color_info = self._get_color_for_height(height_m)

        # Build GeoJSON
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [coordinates]  # Outer ring in array
            },
            "properties": {
                "antenna_lat": antenna_lat_deg,
                "antenna_lon": antenna_lon_deg,
                "height_m": height_m,
                "coverage_area_km2": coverage_area_km2,
                "color": color_info["color"],
                "opacity": color_info["opacity"]
            }
        }

        geojson = {
            "type": "FeatureCollection",
            "features": [feature]
        }

        return geojson

    def compute_polygon_area(self, coordinates):
        """
        Compute area of polygon in km².

        Uses pyproj Geod.polygon_area() which accounts for Earth ellipsoid.

        Args:
            coordinates (list of [lon, lat]): polygon vertices (NOT closed)

        Returns:
            float: area in km²
        """
        lons = [c[0] for c in coordinates]
        lats = [c[1] for c in coordinates]

        # Geod.polygon_area expects (lons, lats) tuples, returns (area_m2, perimeter_m)
        area_m2, _ = GEOD.polygon_area(lons, lats)
        area_km2 = abs(area_m2) / 1e6  # Convert m² to km²

        return area_km2

    def _get_color_for_height(self, height_m):
        """
        Look up colour and opacity for a given height band.

        Args:
            height_m (float or None): target height in metres

        Returns:
            dict: {"color": hex_string, "opacity": float}
                Returns first matching height band from HEIGHT_BAND_COLORS.
                If height_m is None or exceeds all bands, defaults to highest band.
        """
        if height_m is None:
            height_m = max(HEIGHT_BAND_COLORS.keys())

        # Find the closest height band <= height_m
        valid_heights = [h for h in sorted(HEIGHT_BAND_COLORS.keys()) if h <= height_m]
        if valid_heights:
            selected_height = max(valid_heights)
            return HEIGHT_BAND_COLORS[selected_height]
        else:
            # Default to smallest height band
            smallest_height = min(HEIGHT_BAND_COLORS.keys())
            return HEIGHT_BAND_COLORS[smallest_height]

    def export_geojson(self, geojson_dict, output_path):
        """
        Save GeoJSON to file.

        Args:
            geojson_dict (dict): GeoJSON FeatureCollection
            output_path (str or Path): output file path
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(geojson_dict, f, indent=2)

        logger.info(f"Exported GeoJSON to {output_path}")

    def geojson_to_feature_collection(self, *geojson_dicts):
        """
        Merge multiple GeoJSON FeatureCollections into one.

        Args:
            *geojson_dicts: variable number of GeoJSON FeatureCollections

        Returns:
            dict: merged FeatureCollection with all features
        """
        all_features = []
        for geojson in geojson_dicts:
            if geojson.get("type") == "FeatureCollection":
                all_features.extend(geojson.get("features", []))

        return {
            "type": "FeatureCollection",
            "features": all_features
        }
