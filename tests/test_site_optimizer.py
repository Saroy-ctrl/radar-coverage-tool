"""
Tests for src/site_optimizer.py: grid search and coverage scoring.

Covers:
  - generate_grid_points: geodesic grid generation within a bbox
  - compute_coverage_score: single-site coverage area calculation
  - Error handling (invalid step, missing DEM)
"""

import numpy as np
import pytest
from pyproj import Geod
from src.site_optimizer import generate_grid_points, compute_coverage_score, _ray_bbox_distances


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


# ---------------------------------------------------------------------------
# _ray_bbox_distances tests
# ---------------------------------------------------------------------------

def test_ray_bbox_distances_cardinal():
    """
    Site at exact centre of a bbox.
    North/south rays hit the N/S edges; distances must match the flat-earth
    degree-to-metre conversion within 1%.
    """
    bbox = (49.0, -1.0, 50.8, 1.0)
    ant_lat, ant_lon = 49.9, 0.0   # centre

    azimuths = np.array([0.0, 90.0, 180.0, 270.0])
    dists = _ray_bbox_distances(ant_lat, ant_lon, azimuths, bbox)

    m_per_deg = 111320.0
    expected_N = (50.8 - 49.9) * m_per_deg
    expected_S = (49.9 - 49.0) * m_per_deg

    assert abs(dists[0] - expected_N) / expected_N < 0.01, (
        f"North distance {dists[0]:.0f} m, expected ~{expected_N:.0f} m"
    )
    assert abs(dists[2] - expected_S) / expected_S < 0.01, (
        f"South distance {dists[2]:.0f} m, expected ~{expected_S:.0f} m"
    )


def test_ray_bbox_distances_edge_site():
    """
    Site near the western bbox boundary: westward distance must be small,
    eastward distance must be large.
    """
    bbox = (49.0, -1.0, 50.8, 1.0)
    ant_lat, ant_lon = 49.9, -0.95   # close to west edge

    azimuths = np.array([90.0, 270.0])   # east, west
    dists = _ray_bbox_distances(ant_lat, ant_lon, azimuths, bbox)

    assert dists[1] < 6_000, (
        f"West distance should be < 6 km for near-edge site, got {dists[1]:.0f} m"
    )
    assert dists[0] > 100_000, (
        f"East distance should be > 100 km, got {dists[0]:.0f} m"
    )


# ---------------------------------------------------------------------------
# Capped-radial scoring test
# ---------------------------------------------------------------------------

def _flat_dem_request(lat, lon, max_range_km=150.0):
    """Duck-typed ComputationRequest for flat-terrain scoring tests."""
    _rng = max_range_km   # class body can't see enclosing param by same name
    class Req:
        radar_lat = lat
        radar_lon = lon
        site_elevation_amsl_m = 0.0
        antenna_amsl_m = 30.0
        k_factor = 4 / 3
        max_range_km = _rng
        height_bands_m = [500.0]
        diffraction_guard_deg = 0.0
        dem_path = None
        obstructions_path = None
        azimuth_step_deg = 5.0
        range_step_m = 500.0
        min_beam_deg = -90.0
        max_beam_deg = 90.0
    return Req()


def _flat_dem_arrays(lat_centre, lon_centre, size_deg=6.0):
    """Synthetic all-zero DEM (flat terrain) as (data, transform)."""
    import rasterio.transform
    pixel_deg = 1 / 120.0   # 0.5 arc-minute pixels — fast for tests
    n = int(size_deg / pixel_deg)
    data = np.zeros((n, n), dtype=np.float64)
    transform = rasterio.transform.from_bounds(
        lon_centre - size_deg / 2,
        lat_centre - size_deg / 2,
        lon_centre + size_deg / 2,
        lat_centre + size_deg / 2,
        n, n,
    )
    return data, transform


def test_capped_score_prefers_centre_over_edge():
    """
    On flat terrain with sufficient max_range_km, a site at the bbox centre
    scores >= a site at the bbox western edge under capped-radial scoring.

    With the old uncapped area metric an edge site's large open-side arm
    could match or exceed the centre site. With capping each azimuth's
    contribution is bounded by the distance to the bbox edge, so the centre
    site (which reaches all four edges roughly equally) wins.
    """
    bbox = (55.0, -1.5, 56.5, 0.5)
    centre_lat = (55.0 + 56.5) / 2   # 55.75
    centre_lon = (-1.5 + 0.5) / 2    # -0.5

    edge_lat = centre_lat
    edge_lon = -1.4   # near the west edge (min_lon = -1.5)

    dem, tfm = _flat_dem_arrays(centre_lat, centre_lon, size_deg=6.0)

    score_centre = compute_coverage_score(
        _flat_dem_request(centre_lat, centre_lon),
        dem_data=dem, dem_transform=tfm,
        bbox=bbox,
    )
    score_edge = compute_coverage_score(
        _flat_dem_request(edge_lat, edge_lon),
        dem_data=dem, dem_transform=tfm,
        bbox=bbox,
    )

    assert score_centre >= score_edge, (
        f"Centre site ({score_centre:.1f} km²) should score >= "
        f"edge site ({score_edge:.1f} km²) under capped-radial scoring"
    )
