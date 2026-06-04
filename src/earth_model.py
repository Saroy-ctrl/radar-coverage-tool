"""
Earth Model — K-factor, effective radius, and curvature corrections.

Physical foundation:
- Earth radius: 6,371 km (WGS84 mean)
- Effective radius: R_eff = K * 6,371 km
  - K=4/3 (standard troposphere, default)
  - K=1.0 (optical/vacuum)
  - K=user-defined (special propagation)

Reference: BEL paper (Gupta & Gupta), Cambridge Pixel SPx Radar Coverage Tool.
"""

import numpy as np

# Earth parameters (SI units)
EARTH_RADIUS_M = 6_371_000  # WGS84 mean radius in metres


class EarthModel:
    """
    Effective Earth radius and curvature correction engine.

    All parameters use SI units:
    - distances in metres (m)
    - heights in metres above mean sea level (AMSL, m)
    - angles in radians (rad)
    """

    def __init__(self, k_factor=4/3):
        """
        Initialize Earth model with K-factor.

        Args:
            k_factor (float): Effective Earth radius ratio.
                - 4/3 (default): standard troposphere
                - 1.0: optical/vacuum
                - custom: user-defined propagation conditions

        Physical meaning: accounts for atmospheric refraction bending radio waves.
        Higher K → longer horizon range (curved path follows Earth curvature more).
        """
        self.k = k_factor
        self.r_eff_m = k_factor * EARTH_RADIUS_M

    def set_k_factor(self, k_factor):
        """
        Update K-factor and recalculate effective radius.

        Args:
            k_factor (float): new K-factor value
        """
        self.k = k_factor
        self.r_eff_m = k_factor * EARTH_RADIUS_M

    def curvature_correction(self, range_m):
        """
        Compute Earth curvature correction (drop of terrain due to Earth curvature).

        The horizon line is curved. A point on the ground at distance range_m
        from the antenna appears lower than it would on a flat Earth.

        Formula (BEL paper):
            delta_h = d² / (2 * R_eff)

        Args:
            range_m (float or np.ndarray): ground range in metres

        Returns:
            float or np.ndarray: correction in metres (always positive)

        Example:
            At 50 km range, K=4/3: delta_h ≈ 94 m (significant!)
            At 10 km range, K=4/3: delta_h ≈ 3.8 m

        Usage:
            h_apparent = h_terrain_amsl - earth_model.curvature_correction(range_m)
        """
        return (range_m ** 2) / (2.0 * self.r_eff_m)

    def radar_horizon(self, height_amsl_m):
        """
        Compute radar horizon range (distance to geometric horizon).

        The radio beam propagates in a curved path (refraction). The horizon is
        the range where the beam grazes the surface of the effective Earth sphere.

        Formula (BEL paper Eq. 2):
            R_horizon = sqrt(2 * R_eff * h)

        Args:
            height_amsl_m (float or np.ndarray): antenna height above mean sea level (m)

        Returns:
            float or np.ndarray: horizon range in metres

        Example:
            At h=100 m, K=4/3: R_horizon ≈ 37.8 km
            At h=10 m, K=4/3: R_horizon ≈ 12.0 km

        Physical meaning: This is the unobstructed range assuming flat, zero-elevation terrain.
        Any target at this range at the same height is just visible (grazing incidence).
        """
        return np.sqrt(2.0 * self.r_eff_m * height_amsl_m)

    def total_range(self, h_antenna_amsl_m, h_target_amsl_m):
        """
        Compute total range (antenna to target, both visible to each other).

        When both antenna and target are above ground level, their horizons add:

        Formula (BEL paper Eq. 3):
            R_total = R_horizon(h_antenna) + R_horizon(h_target)

        Args:
            h_antenna_amsl_m (float): antenna height AMSL (m)
            h_target_amsl_m (float or np.ndarray): target height AMSL (m)

        Returns:
            float or np.ndarray: total range in metres

        Example:
            Both at h=100 m, K=4/3: R_total ≈ 75.6 km
            Antenna 100 m, target 1000 m: R_total ≈ 123 km

        Physical meaning: Maximum unobstructed range between two points.
        Actual range is limited by terrain obstacles.
        """
        r_h_antenna = self.radar_horizon(h_antenna_amsl_m)
        r_h_target = self.radar_horizon(h_target_amsl_m)
        return r_h_antenna + r_h_target
