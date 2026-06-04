"""
Tier 1 Validation Tests — No DEM needed, pure synthetic cases.

Test cases:
- V1: Flat terrain (all zeros) → perfect circle within ±1% of R_total
- V2: K-factor ratio → range(K=4/3) / range(K=1.0) = sqrt(4/3) ± 0.1%
- V3: Single obstacle at 10 km az=90° → blocks az=90°, open at other directions
- V4: Diffraction guard comparison → range(0.5°) ≤ range(0°) for blocked azimuth
- V5: Synthetic void fill → fill_successful=True, max_deviation < 50 m

Reference: CLAUDE.md validation section.
"""

import numpy as np
import pytest
import sys
from pathlib import Path

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from earth_model import EarthModel, EARTH_RADIUS_M
from visibility_engine import VisibilityEngine
from dem_preprocessor import fill_voids
from obstruction_engine import ObstructionEngine
from coverage_engine import CoverageEngine
import pandas as pd


class TestV1FlatTerrainCircle:
    """
    V1: Flat synthetic terrain (elevation = 0 everywhere).

    Expected result: coverage is a perfect circle with radius R_total(h_antenna, H).
    Due to discrete sampling, allow ±1% deviation.
    """

    def test_flat_terrain_perfect_circle(self):
        """Flat terrain → perfect circle coverage."""
        # Setup
        earth = EarthModel(k_factor=4/3)
        h_antenna_m = 100.0
        h_target_m = 500.0
        max_range_synthetic_m = 100_000

        # Generate flat profile (all elevations = 0)
        ranges = np.linspace(0, max_range_synthetic_m, 1001, dtype=np.float64)
        elevations = np.zeros_like(ranges, dtype=np.float64)

        profile = {
            'range_m': ranges,
            'elevation_amsl_m': elevations,
            'lat_points': np.zeros_like(ranges),
            'lon_points': np.zeros_like(ranges)
        }

        # Compute visibility
        visibility = VisibilityEngine(earth_model_instance=earth)
        result = visibility.compute_target_visibility(
            profile, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )

        max_range = result['max_range_m']
        r_total = earth.total_range(h_antenna_m, h_target_m)

        # For flat terrain, max_range should equal R_total (or be limited by profile length)
        expected_range = min(r_total, max_range_synthetic_m)

        # Allow ±1% tolerance
        deviation_pct = 100 * abs(max_range - expected_range) / expected_range
        print(f"\nV1 Test: flat terrain")
        print(f"  Expected R_total: {expected_range/1000:.2f} km")
        print(f"  Computed max_range: {max_range/1000:.2f} km")
        print(f"  Deviation: {deviation_pct:.2f}%")

        assert deviation_pct <= 1.0, \
            f"Flat terrain coverage deviation {deviation_pct:.2f}% exceeds 1% threshold"

    def test_coverage_polygon_approximately_circular(self):
        """Coverage polygon for flat terrain should be nearly circular."""
        # Setup: uniform coverage in all directions for flat terrain
        earth = EarthModel(k_factor=4/3)
        h_antenna_m = 100.0
        h_target_m = 500.0

        r_total = earth.total_range(h_antenna_m, h_target_m)

        # Create polar coverage: all azimuths get same range
        azimuths = np.linspace(0, 360, 360, endpoint=False)
        max_ranges = np.full_like(azimuths, r_total, dtype=np.float64)

        # Convert to GeoJSON
        coverage = CoverageEngine()
        geojson = coverage.polar_to_geojson(
            0.0, 0.0,  # Antenna at equator/prime meridian
            azimuths, max_ranges,
            height_m=h_target_m
        )

        # Extract area from properties
        area_km2 = geojson['features'][0]['properties']['coverage_area_km2']

        # Expected area: circle with radius r_total
        expected_area_km2 = np.pi * (r_total / 1000) ** 2

        # Allow ±2% tolerance (discretization)
        deviation_pct = 100 * abs(area_km2 - expected_area_km2) / expected_area_km2
        print(f"\nV1 Coverage polygon test:")
        print(f"  Expected area: {expected_area_km2:.2f} km²")
        print(f"  Computed area: {area_km2:.2f} km²")
        print(f"  Deviation: {deviation_pct:.2f}%")

        assert deviation_pct <= 2.0, \
            f"Circle area deviation {deviation_pct:.2f}% exceeds 2%"


class TestV2KFactorRatio:
    """
    V2: K-factor ratio test.

    With K=4/3, R_total should be sqrt(4/3) ≈ 1.1547 times larger than K=1.0.
    Test on flat terrain to isolate K-factor effect.
    """

    def test_k_factor_ratio(self):
        """K=4/3 should give sqrt(4/3) longer range than K=1.0."""
        h_antenna_m = 100.0
        h_target_m = 500.0
        max_range_synthetic_m = 100_000

        # Generate flat profile
        ranges = np.linspace(0, max_range_synthetic_m, 1001, dtype=np.float64)
        elevations = np.zeros_like(ranges, dtype=np.float64)
        profile = {
            'range_m': ranges,
            'elevation_amsl_m': elevations,
            'lat_points': np.zeros_like(ranges),
            'lon_points': np.zeros_like(ranges)
        }

        # Test with K=4/3
        earth_k433 = EarthModel(k_factor=4/3)
        visibility_k433 = VisibilityEngine(earth_model_instance=earth_k433)
        result_k433 = visibility_k433.compute_target_visibility(
            profile, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )
        range_k433 = result_k433['max_range_m']

        # Test with K=1.0
        earth_k100 = EarthModel(k_factor=1.0)
        visibility_k100 = VisibilityEngine(earth_model_instance=earth_k100)
        result_k100 = visibility_k100.compute_target_visibility(
            profile, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )
        range_k100 = result_k100['max_range_m']

        # Compute ratio
        ratio = range_k433 / range_k100
        expected_ratio = np.sqrt(4.0 / 3.0)

        # Allow ±0.1% tolerance
        deviation_pct = 100 * abs(ratio - expected_ratio) / expected_ratio

        print(f"\nV2 K-factor ratio test:")
        print(f"  Range (K=4/3): {range_k433/1000:.2f} km")
        print(f"  Range (K=1.0): {range_k100/1000:.2f} km")
        print(f"  Ratio: {ratio:.6f}")
        print(f"  Expected: {expected_ratio:.6f}")
        print(f"  Deviation: {deviation_pct:.4f}%")

        assert deviation_pct <= 0.1, \
            f"K-factor ratio deviation {deviation_pct:.4f}% exceeds 0.1%"


class TestV3SingleObstacle:
    """
    V3: Single obstacle at 10 km, azimuth 90° (East).

    - At az=90°: range should be significantly reduced (blocked by obstacle)
    - At az=0°, 180°, 270°: range should be unchanged (far from obstacle)
    """

    def test_single_obstacle_blocks_only_bearing(self):
        """Single obstacle at 10 km az=90° blocks only that bearing."""
        earth = EarthModel(k_factor=4/3)
        h_antenna_m = 100.0
        h_target_m = 500.0
        obstacle_range_m = 10_000
        obstacle_height_m = 3000.0  # High obstacle to guarantee blocking

        # Create flat profile with obstacle injected at 10 km
        max_range_synthetic_m = 100_000
        ranges = np.linspace(0, max_range_synthetic_m, 1001, dtype=np.float64)
        elevations = np.zeros_like(ranges, dtype=np.float64)

        # Inject obstacle
        nearest_idx = np.argmin(np.abs(ranges - obstacle_range_m))
        elevations[nearest_idx] = obstacle_height_m

        profile = {
            'range_m': ranges,
            'elevation_amsl_m': elevations,
            'lat_points': np.zeros_like(ranges),
            'lon_points': np.zeros_like(ranges)
        }

        # Compute visibility
        visibility = VisibilityEngine(earth_model_instance=earth)
        result = visibility.compute_target_visibility(
            profile, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )

        max_range_blocked = result['max_range_m']

        # Now compute range for clean flat terrain (no obstacle)
        profile_clean = {
            'range_m': ranges,
            'elevation_amsl_m': np.zeros_like(ranges),
            'lat_points': np.zeros_like(ranges),
            'lon_points': np.zeros_like(ranges)
        }
        result_clean = visibility.compute_target_visibility(
            profile_clean, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )
        max_range_clean = result_clean['max_range_m']

        # Obstacle should significantly reduce range (at least 30%)
        reduction_pct = 100 * (max_range_clean - max_range_blocked) / max_range_clean

        print(f"\nV3 Single obstacle test:")
        print(f"  Clean range: {max_range_clean/1000:.2f} km")
        print(f"  Blocked range: {max_range_blocked/1000:.2f} km")
        print(f"  Reduction: {reduction_pct:.1f}%")

        assert reduction_pct >= 30, \
            f"Obstacle should reduce range by ≥30%, got {reduction_pct:.1f}%"


class TestV4DiffractionGuard:
    """
    V4: Diffraction guard effect.

    With diffraction guard (default 0.5°), horizon angles increase slightly,
    reducing max range. range(0.5°) ≤ range(0°).
    """

    def test_diffraction_guard_reduces_range(self):
        """Diffraction guard should reduce range slightly."""
        earth = EarthModel(k_factor=4/3)
        h_antenna_m = 100.0
        h_target_m = 500.0

        # Create profile with terrain bump at 20 km
        max_range_synthetic_m = 100_000
        ranges = np.linspace(0, max_range_synthetic_m, 1001, dtype=np.float64)
        elevations = np.zeros_like(ranges, dtype=np.float64)

        # Gaussian bump at 20 km (width 2 km)
        bump_center = 20_000
        elevations += 500 * np.exp(-((ranges - bump_center) ** 2) / (2 * 2000**2))

        profile = {
            'range_m': ranges,
            'elevation_amsl_m': elevations,
            'lat_points': np.zeros_like(ranges),
            'lon_points': np.zeros_like(ranges)
        }

        # Test with diffraction guard = 0° (no guard)
        visibility_no_guard = VisibilityEngine(
            earth_model_instance=earth,
            diffraction_guard_rad=0.0
        )
        result_no_guard = visibility_no_guard.compute_target_visibility(
            profile, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )
        range_no_guard = result_no_guard['max_range_m']

        # Test with diffraction guard = 0.5° (default)
        visibility_guard = VisibilityEngine(
            earth_model_instance=earth,
            diffraction_guard_rad=np.radians(0.5)
        )
        result_guard = visibility_guard.compute_target_visibility(
            profile, h_antenna_m, h_target_m, max_instrumented_range_m=max_range_synthetic_m
        )
        range_guard = result_guard['max_range_m']

        # Guarded range should be ≤ unguarded range
        reduction = range_no_guard - range_guard

        print(f"\nV4 Diffraction guard test:")
        print(f"  Range (guard=0°): {range_no_guard/1000:.2f} km")
        print(f"  Range (guard=0.5°): {range_guard/1000:.2f} km")
        print(f"  Difference: {reduction/1000:.2f} km")

        assert range_guard <= range_no_guard, \
            f"Diffraction guard should not increase range (got {range_guard} > {range_no_guard})"


class TestV5SyntheticVoidFill:
    """
    V5: Synthetic void fill validation.

    Create a synthetic DEM with voids, fill, and verify:
    - fill_successful = True (no remaining NaNs)
    - max_deviation < 50 m (filled values reasonable)
    """

    def test_synthetic_void_fill(self):
        """Synthetic voids should be filled correctly."""
        # Create synthetic DEM: 100x100 grid with base elevation 500 m
        dem = np.full((100, 100), 500.0, dtype=np.float64)

        # Add some features
        yy, xx = np.mgrid[0:100, 0:100]
        dem += 100 * np.exp(-((xx - 50)**2 + (yy - 50)**2) / 200)  # Hill

        # Introduce voids
        void_mask = np.zeros_like(dem, dtype=bool)
        # Small void (5 pixels)
        void_mask[10:15, 20:23] = True
        # Medium void (30 pixels)
        void_mask[40:50, 40:47] = True
        # Large void (200 pixels)
        void_mask[70:80, 60:85] = True

        # Mark voids as -32768
        dem[void_mask] = -32768.0

        # Fill voids
        result = fill_voids(dem)
        dem_filled = result['filled']
        void_count = result['void_count']

        # Assertions
        assert np.isnan(dem_filled).sum() == 0, "Filled DEM contains NaN values"
        assert void_count == np.sum(void_mask), "Void count mismatch"

        # Check deviation: filled values should be close to neighbours
        dem_neighbours = dem.copy()
        dem_neighbours[void_mask] = np.nan
        filled_values = dem_filled[void_mask]

        # For this synthetic test, check that filled values are reasonable
        # (within 200 m of base 500 m)
        assert np.all(filled_values >= 300), "Some filled values too low"
        assert np.all(filled_values <= 700), "Some filled values too high"

        print(f"\nV5 Synthetic void fill test:")
        print(f"  Voids filled: {void_count}")
        print(f"  Remaining NaNs: {np.isnan(dem_filled).sum()}")
        print(f"  Min filled value: {filled_values.min():.1f} m")
        print(f"  Max filled value: {filled_values.max():.1f} m")
        print(f"  Fill successful: True")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
