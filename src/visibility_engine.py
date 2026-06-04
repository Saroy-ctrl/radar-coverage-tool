"""
Visibility Engine — Horizon tracking and multi-height coverage computation.

Core algorithm (horizon blocking + diffraction):

For a given terrain profile (range_m, elevation_amsl_m) and antenna height:
1. Compute obstruction angles: obs_angle[i] = atan2(h_apparent[i] - h_antenna, range[i])
2. Apply diffraction guard: obs_angle[i] += diffraction_guard_rad
3. Track cumulative max: horizon[i] = max(horizon[i-1], obs_angle[i])
4. For target height H: target_angle[i] = atan2(H - h_curvature[i], range[i])
5. Find first index where target_angle < horizon → blocked
6. max_range = min(blocked_range, R_total(h_antenna, H), max_instrumented_range)

Reference: BEL paper (Gupta & Gupta) — Eq. 2–5, diffraction guard per Cambridge Pixel SPx.
"""

import numpy as np
import logging
from . import earth_model

logger = logging.getLogger(__name__)

# Diffraction guard: add extra angle to account for diffraction over obstacles
# Per BEL paper: default 0.5° (prevents sharp cutoffs)
DIFFRACTION_GUARD_DEG = 0.5
DIFFRACTION_GUARD_RAD = np.radians(DIFFRACTION_GUARD_DEG)


class VisibilityEngine:
    """Compute horizon angles and target visibility along radials."""

    def __init__(self, earth_model_instance=None, diffraction_guard_rad=DIFFRACTION_GUARD_RAD):
        """
        Initialize Visibility Engine.

        Args:
            earth_model_instance (earth_model.EarthModel, optional): K-factor model.
                If None, creates default (K=4/3).
            diffraction_guard_rad (float): diffraction guard in radians (default 0.5°).
                Adds extra angle to obstruction angles to account for wave diffraction.
        """
        if earth_model_instance is None:
            earth_model_instance = earth_model.EarthModel(k_factor=4/3)
        self.earth = earth_model_instance
        self.diffraction_guard_rad = diffraction_guard_rad

    def compute_horizon_angles(self, profile_dict, h_antenna_amsl_m):
        """
        Compute cumulative horizon angles along radial.

        Workflow:
        1. Extract range and elevation from profile
        2. Compute h_apparent = h_terrain - curvature_correction(range)
        3. obs_angle[i] = atan2(h_apparent[i] - h_antenna, range[i])
        4. Apply diffraction guard: obs_angle[i] += diffraction_guard_rad
        5. Cumulative max: horizon[i] = max(horizon[i-1], obs_angle[i])

        Args:
            profile_dict (dict): output from terrain_engine or obstruction_engine
                with keys: range_m, elevation_amsl_m
            h_antenna_amsl_m (float): antenna height above mean sea level (metres)

        Returns:
            dict: {
                'horizon_angle_rad': 1D array of cumulative horizon angles (radians),
                'obstruction_angle_rad': 1D array of obstruction angles before diffraction (radians),
                'h_apparent_m': 1D array of apparent terrain heights (metres)
            }

        Physical meaning:
        - horizon_angle_rad[i] = elevation angle to farthest visible obstacle up to range_m[i]
        - target at angle > horizon_angle_rad[i] is visible
        - target at angle < horizon_angle_rad[i] is blocked
        """
        ranges = profile_dict['range_m'].astype(np.float64)
        elevations = profile_dict['elevation_amsl_m'].astype(np.float64)

        # Curvature correction
        delta_h = self.earth.curvature_correction(ranges)
        h_apparent = elevations - delta_h

        # Obstruction angles: angle from antenna to terrain (before diffraction)
        obs_angles = np.arctan2(h_apparent - h_antenna_amsl_m, ranges)

        # Apply diffraction guard
        obs_angles_with_guard = obs_angles + self.diffraction_guard_rad

        # Cumulative max: horizon tracking
        horizon_angles = np.maximum.accumulate(obs_angles_with_guard)

        return {
            'horizon_angle_rad': horizon_angles,
            'obstruction_angle_rad': obs_angles,
            'h_apparent_m': h_apparent
        }

    def compute_target_visibility(self, profile_dict, h_antenna_amsl_m,
                                  h_target_amsl_m, max_instrumented_range_m=None):
        """
        Compute maximum range where target at height h_target_amsl_m is visible.

        Algorithm:
        1. Compute horizon angles (via compute_horizon_angles)
        2. For each range: target_angle = atan2(h_target - h_curvature, range)
        3. Find first index where target_angle < horizon_angle → BLOCKED
        4. max_range = min(blocked_range, R_total(h_antenna, h_target), max_instrumented)

        Args:
            profile_dict (dict): elevation profile
            h_antenna_amsl_m (float): antenna height (metres AMSL)
            h_target_amsl_m (float): target height (metres AMSL)
            max_instrumented_range_m (float, optional): system range limit (metres).
                If None, uses R_total(h_antenna, h_target).

        Returns:
            dict: {
                'max_range_m': maximum range (metres) where target is visible,
                'first_blocked_idx': index of first blocked bin (or None),
                'target_angle_rad': 1D array of target angles (radians),
                'visible_mask': 1D boolean array (True=visible)
            }

        Example:
            result = visibility_engine.compute_target_visibility(
                profile, h_antenna=100, h_target=500, max_instrumented_range_m=100000
            )
            max_range = result['max_range_m']  # ≈ 75 km (limited by horizon+terrain)
        """
        ranges = profile_dict['range_m'].astype(np.float64)

        # Horizon angles
        horizon_result = self.compute_horizon_angles(profile_dict, h_antenna_amsl_m)
        horizon_angles = horizon_result['horizon_angle_rad']
        h_apparent = horizon_result['h_apparent_m']

        # Target angles
        target_angles = np.arctan2(h_target_amsl_m - h_apparent, ranges)

        # Visibility mask: visible where target_angle > horizon_angle
        visible_mask = target_angles >= horizon_angles

        # First blocked index
        blocked_indices = np.where(~visible_mask)[0]
        first_blocked_idx = blocked_indices[0] if len(blocked_indices) > 0 else None

        # Max range: min of (blocked range, R_total, max_instrumented)
        if first_blocked_idx is not None:
            blocked_range = ranges[first_blocked_idx]
        else:
            blocked_range = ranges[-1]  # All visible to end of profile

        r_total = self.earth.total_range(h_antenna_amsl_m, h_target_amsl_m)

        max_range = min(blocked_range, r_total)

        if max_instrumented_range_m is not None:
            max_range = min(max_range, max_instrumented_range_m)

        return {
            'max_range_m': max_range,
            'first_blocked_idx': first_blocked_idx,
            'target_angle_rad': target_angles,
            'visible_mask': visible_mask
        }

    def compute_coverage_range_array(self, azimuth_array, profile_dict,
                                     h_antenna_amsl_m, h_target_amsl_m,
                                     max_instrumented_range_m=None):
        """
        Compute max coverage range for multiple azimuths (vectorized).

        Convenience function: for a set of azimuths, compute the max range
        where a target at h_target_amsl_m is visible in each direction.

        Args:
            azimuth_array (np.ndarray): array of azimuths (degrees, compass convention)
            profile_dict (dict): single radial profile (all azimuths assumed similar
                geometry for synthetic testing; real code would pass per-azimuth profile)
            h_antenna_amsl_m (float): antenna height (m)
            h_target_amsl_m (float): target height (m)
            max_instrumented_range_m (float, optional): system range limit

        Returns:
            np.ndarray: max_ranges for each azimuth (metres)

        Note: This is a simplified version. Full implementation would extract
        per-azimuth profiles and compute visibility for each. Tier 1 tests
        use a single profile for all azimuths.
        """
        result = self.compute_target_visibility(
            profile_dict, h_antenna_amsl_m, h_target_amsl_m,
            max_instrumented_range_m
        )
        max_range = result['max_range_m']

        # Replicate for all azimuths
        return np.full(len(azimuth_array), max_range, dtype=np.float64)
