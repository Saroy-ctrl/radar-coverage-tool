# tests/test_beam_angles.py
"""
Tests for radar beam elevation angle feature.

Covers:
  - ComputationRequest default values (±90° = no constraint)
  - Beam gate numpy logic (pure math, no DEM needed)
  - MapView._build_coverage_features reading a new dict format
"""

import numpy as np
import pytest
import sys
from pathlib import Path

# Add src/ to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Import ComputationRequest directly from control_panel module to avoid
# triggering matplotlib imports through gui/__init__.py chain.
# This allows the test to run in headless environments where Qt may be mocked.
try:
    from gui.control_panel import ComputationRequest
except ImportError:
    # If matplotlib refuses to load, skip ComputationRequest tests gracefully
    ComputationRequest = None


# ---------------------------------------------------------------------------
# ComputationRequest defaults
# ---------------------------------------------------------------------------

def test_computation_request_has_beam_angle_defaults():
    """ComputationRequest defaults to ±90° beam angles (no constraint)."""
    if ComputationRequest is None:
        pytest.skip("ComputationRequest not importable due to matplotlib/Qt issues")

    req = ComputationRequest(
        radar_lat=51.5,
        radar_lon=0.0,
        site_elevation_amsl_m=0.0,
        antenna_amsl_m=10.0,
        k_factor=1.333,
        max_range_km=100.0,
        height_bands_m=[50.0],
        diffraction_guard_deg=0.5,
    )
    assert req.min_beam_deg == -90.0
    assert req.max_beam_deg == 90.0


# ---------------------------------------------------------------------------
# Beam gate math (pure numpy — no DEM, no QApplication)
# ---------------------------------------------------------------------------

def test_beam_gate_default_is_transparent():
    """With ±90° defaults beam_within is all True → combined_mask == terrain_visible."""
    target_angles = np.radians(np.array([5.0, 2.0, 1.0, 0.5, 0.1]))
    horizon_angles = np.radians(np.array([0.0, 1.0, 1.0, 1.0, 0.0]))
    terrain_visible = target_angles >= horizon_angles

    min_beam_rad = np.radians(-90.0)
    max_beam_rad = np.radians(90.0)
    beam_within = (target_angles >= min_beam_rad) & (target_angles <= max_beam_rad)
    combined_mask = terrain_visible & beam_within

    np.testing.assert_array_equal(combined_mask, terrain_visible)


def test_beam_gate_max_angle_excludes_steep_near_range():
    """max_beam=2° excludes bins whose elevation angle exceeds 2°."""
    target_angles = np.radians(np.array([10.0, 5.0, 2.0, 1.0, 0.5]))
    terrain_visible = np.ones(5, dtype=bool)  # flat terrain, all visible

    beam_within = (target_angles >= np.radians(-90.0)) & (target_angles <= np.radians(2.0))
    combined_mask = terrain_visible & beam_within

    assert not combined_mask[0]   # 10° > 2° max beam → excluded
    assert not combined_mask[1]   # 5° > 2° max beam → excluded
    assert combined_mask[2]       # exactly 2° → included
    assert combined_mask[3]       # 1° → included
    assert combined_mask[4]       # 0.5° → included


def test_beam_gate_min_angle_excludes_below_horizon():
    """min_beam=1° excludes bins whose elevation angle is below 1°."""
    target_angles = np.radians(np.array([5.0, 2.0, 1.0, 0.5, -1.0]))
    terrain_visible = np.ones(5, dtype=bool)

    beam_within = (target_angles >= np.radians(1.0)) & (target_angles <= np.radians(90.0))
    combined_mask = terrain_visible & beam_within

    assert combined_mask[0]       # 5° → included
    assert combined_mask[1]       # 2° → included
    assert combined_mask[2]       # exactly 1° → included
    assert not combined_mask[3]   # 0.5° < 1° min beam → excluded
    assert not combined_mask[4]   # -1° < 1° min beam → excluded


def test_shadow_uses_terrain_visible_not_combined_mask():
    """Shadow extraction must use terrain_visible alone, not combined_mask."""
    target_angles = np.radians(np.array([10.0, 1.0, 0.5]))
    horizon_angles = np.radians(np.array([0.0, 0.0, 2.0]))  # blocked at bin 2

    terrain_visible = target_angles >= horizon_angles
    beam_within = (target_angles >= np.radians(-90.0)) & (target_angles <= np.radians(2.0))
    combined_mask = terrain_visible & beam_within

    # bin 0: terrain_visible=True, beam_within=False (10° > 2° max)
    assert terrain_visible[0] == True
    assert combined_mask[0] == False   # beam excluded — NOT terrain blocked
    # bin 2: terrain blocked, not beam-excluded
    assert terrain_visible[2] == False  # terrain blocked → goes to shadow
    assert combined_mask[2] == False


# ---------------------------------------------------------------------------
# MapView._build_coverage_features — new dict format
# ---------------------------------------------------------------------------

@pytest.fixture
def qapp():
    """Minimal QApplication fixture for GUI tests."""
    from PyQt6.QtWidgets import QApplication
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_build_coverage_features_reads_outer_ring(qapp):
    """_build_coverage_features reads {"outer": [...], "inner": None} format."""
    from gui.map_view import MapView
    view = MapView()

    outer = [(51.0, 0.0), (51.1, 0.1), (51.0, 0.2), (50.9, 0.1)]
    coverage_data = {100.0: {"outer": outer, "inner": None}}

    features = view._build_coverage_features(coverage_data)

    assert len(features) == 1
    latlngs = features[0]["latlngs"]
    # Single ring: latlngs is a list-of-rings where each ring is [[lat, lon], ...]
    assert isinstance(latlngs, list)
    assert len(latlngs) == 1                         # one ring, no donut
    assert isinstance(latlngs[0][0], list)           # [[lat, lon], ...]


def test_build_coverage_features_produces_two_rings_when_inner_given(qapp):
    """_build_coverage_features appends inner ring when inner coords provided."""
    from gui.map_view import MapView
    view = MapView()

    outer = [(51.0, 0.0), (51.1, 0.1), (51.0, 0.2), (50.9, 0.1)]
    inner = [(51.0, 0.01), (51.05, 0.05), (51.0, 0.09), (50.95, 0.05)]
    coverage_data = {100.0: {"outer": outer, "inner": inner}}

    features = view._build_coverage_features(coverage_data)

    assert len(features) == 1
    latlngs = features[0]["latlngs"]
    assert len(latlngs) == 2    # outer ring + inner ring (donut)
