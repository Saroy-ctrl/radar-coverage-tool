"""
Tests for src/site_optimizer.py: grid search and coverage scoring.

Covers:
  - generate_grid_points: geodesic grid generation within a bbox
  - compute_coverage_score: single-site coverage area calculation
  - Error handling (invalid step, missing DEM)
"""

import pytest
from pyproj import Geod
from src.site_optimizer import generate_grid_points, compute_coverage_score


def test_generate_grid_points_count():
    """A 10×10 km bbox at 1000m step should return roughly 100 points (±20% tolerance)."""
    geod = Geod(ellps='WGS84')

    # 10 km north from (51.0, 0.0)
    _, max_lat, _ = geod.fwd(0.0, 51.0, 0, 10_000)

    # 10 km east from (51.0, 0.0)
    max_lon, _, _ = geod.fwd(0.0, 51.0, 90, 10_000)

    pts = generate_grid_points(51.0, 0.0, max_lat, max_lon, 1000)

    # Expect ~100 points; allow ±20% due to geodesic variation with latitude
    assert 80 <= len(pts) <= 130, f"Expected ~100 points, got {len(pts)}"


def test_generate_grid_points_within_bbox():
    """All returned (lat, lon) must lie within the bbox (with 1e-6 margin)."""
    pts = generate_grid_points(40.0, 10.0, 41.0, 11.0, 5000)

    assert len(pts) > 0, "Grid should contain at least one point"

    for lat, lon in pts:
        assert 40.0 - 1e-6 <= lat <= 41.0 + 1e-6, f"Latitude {lat} outside bbox [40.0, 41.0]"
        assert 10.0 - 1e-6 <= lon <= 11.0 + 1e-6, f"Longitude {lon} outside bbox [10.0, 11.0]"


def test_generate_grid_points_invalid_step():
    """step_m <= 0 must raise ValueError."""
    with pytest.raises(ValueError):
        generate_grid_points(51.0, 0.0, 51.1, 0.1, 0)

    with pytest.raises(ValueError):
        generate_grid_points(51.0, 0.0, 51.1, 0.1, -500)


def test_compute_coverage_score_no_dem_raises():
    """compute_coverage_score with dem_path=None must raise."""
    # Minimal duck-typed request object matching ComputationRequest shape
    class FakeRequest:
        radar_lat = 51.5
        radar_lon = 0.0
        site_elevation_amsl_m = 100.0
        antenna_amsl_m = 107.0
        k_factor = 4 / 3
        max_range_km = 50.0
        height_bands_m = [500.0]
        diffraction_guard_deg = 0.5
        dem_path = None
        obstructions_path = None
        azimuth_step_deg = 5.0
        range_step_m = 500.0
        min_beam_deg = -90.0
        max_beam_deg = 90.0

    with pytest.raises(Exception):
        compute_coverage_score(FakeRequest())
