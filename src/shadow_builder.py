# src/shadow_builder.py
"""
Shadow geometry builder.

Public functions:
  extract_blocked_segments   — run-length encode blocked range bins for one azimuth.
  build_merged_shadow_geojson — convert shadow segments to merged GeoJSON with shapely unions.
"""

import numpy as np
from pyproj import Geod
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

GEOD = Geod(ellps='WGS84')
MIN_INNER_R_M = 200.0   # prevent degenerate point polygon at antenna


def extract_blocked_segments(visible_mask, ranges, az_deg):
    """
    Return all contiguous blocked intervals for one azimuth radial.

    Args:
        visible_mask: bool ndarray shape (N,) — True = visible.
                      Index j corresponds to ranges[j+1].
        ranges:       float ndarray shape (N+1,) — ranges[0]=0, ranges[1..N]=range bins.
        az_deg:       azimuth of this radial (degrees, 0=North)

    Returns:
        list of (az_deg: float, inner_r_m: float, outer_r_m: float)
        Empty list if no blocked bins exist.
    """
    blocked = (~visible_mask).astype(np.int8)
    if blocked.sum() == 0:
        return []

    # Pad with sentinel zeros so np.diff detects runs touching the boundary
    padded = np.concatenate([[0], blocked, [0]])
    diffs  = np.diff(padded)                          # length len(blocked)+1

    starts = np.where(diffs == 1)[0]   # index in blocked where run begins
    ends   = np.where(diffs == -1)[0]  # index in blocked just past run end

    # blocked[s]   → ranges[s+1]   (first blocked bin)
    # blocked[e-1] → ranges[e]     (last blocked bin)
    result = []
    for s, e in zip(starts, ends):
        inner_r = max(float(ranges[s + 1]), MIN_INNER_R_M)
        outer_r = float(ranges[e])
        # Single-bin run: outer_r == ranges[s+1] == inner_r before clamp — advance one step.
        if outer_r <= inner_r and e + 1 < len(ranges):
            outer_r = float(ranges[e + 1])
        if outer_r > inner_r:
            result.append((float(az_deg), inner_r, outer_r))
    return result


def _wedge_polygon(ant_lat, ant_lon, az_deg, inner_r, outer_r, half_az_deg):
    """
    Build a shapely Polygon for one (azimuth, inner_r, outer_r) shadow segment.
    Uses 4 geodetic corners so adjacent azimuths share edges and union cleanly.
    """
    left_az  = (az_deg - half_az_deg) % 360
    right_az = (az_deg + half_az_deg) % 360

    lon_il, lat_il, _ = GEOD.fwd(ant_lon, ant_lat, left_az,  inner_r)
    lon_ir, lat_ir, _ = GEOD.fwd(ant_lon, ant_lat, right_az, inner_r)
    lon_ol, lat_ol, _ = GEOD.fwd(ant_lon, ant_lat, left_az,  outer_r)
    lon_or, lat_or, _ = GEOD.fwd(ant_lon, ant_lat, right_az, outer_r)

    return Polygon([
        (lon_il, lat_il),
        (lon_ol, lat_ol),
        (lon_or, lat_or),
        (lon_ir, lat_ir),
        (lon_il, lat_il),
    ])


def build_merged_shadow_geojson(ant_lat, ant_lon, shadow_segments, azimuth_step_deg):
    """
    Convert shadow segments to a GeoJSON FeatureCollection with smooth merged polygons.

    Adjacent azimuth wedges covering the same terrain feature are united by
    shapely.unary_union into organic blob shapes, matching Cambridge Pixel's
    appearance.

    Args:
        ant_lat, ant_lon:    antenna WGS84 position
        shadow_segments:     list of (az_deg, inner_r_m, outer_r_m)
        azimuth_step_deg:    azimuth resolution used in the computation run

    Returns:
        dict: GeoJSON FeatureCollection (Polygon or MultiPolygon geometry)
    """

    if not shadow_segments:
        return {"type": "FeatureCollection", "features": []}

    half = azimuth_step_deg / 2.0
    polys = [
        _wedge_polygon(ant_lat, ant_lon, az, inner_r, outer_r, half)
        for az, inner_r, outer_r in shadow_segments
    ]
    valid_polys = [p for p in polys if p.is_valid and not p.is_empty]
    if not valid_polys:
        return {"type": "FeatureCollection", "features": []}

    merged = unary_union(valid_polys)
    if merged.is_empty:
        return {"type": "FeatureCollection", "features": []}

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(merged),
            "properties": {}
        }]
    }
