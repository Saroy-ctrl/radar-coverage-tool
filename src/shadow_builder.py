# src/shadow_builder.py
"""
Shadow geometry builder.

Public functions:
  extract_blocked_segments — run-length encode blocked range bins for one azimuth.

Additional geometry builders (e.g. merged GeoJSON, shapely unions) will be added
in future phases.
"""

import numpy as np
from pyproj import Geod

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
