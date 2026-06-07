# tests/test_beam_angles.py
"""
Tests for radar beam elevation angle feature.

Covers:
  - ComputationRequest default values (±90° = no constraint)
  - Beam gate numpy logic (pure math, no DEM needed)
  - MapView._build_coverage_features reading new dict format

Imports use importlib.util to load individual modules directly,
bypassing src/gui/__init__.py (which triggers polar_view → matplotlib
→ Qt version check that breaks headless test environments).
"""

import importlib.util
import pathlib
import sys
import numpy as np

_ROOT = pathlib.Path(__file__).parent.parent


def _load(rel_path: str, name: str):
    """Load a single source file as a module, bypassing package __init__.py."""
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load control_panel directly (avoids gui/__init__ → main_window → polar_view → matplotlib)
_cp = _load("src/gui/control_panel.py", "control_panel")
ComputationRequest = _cp.ComputationRequest


# ---------------------------------------------------------------------------
# ComputationRequest defaults
# ---------------------------------------------------------------------------

def test_computation_request_has_beam_angle_defaults():
    """ComputationRequest defaults to ±90° beam angles (no constraint)."""
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
    terrain_visible = np.ones(5, dtype=bool)

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

    assert combined_mask[0]
    assert combined_mask[1]
    assert combined_mask[2]       # exactly 1° → included
    assert not combined_mask[3]   # 0.5° < min → excluded
    assert not combined_mask[4]   # -1° < min → excluded


def test_shadow_uses_terrain_visible_not_combined_mask():
    """Shadow extraction must use terrain_visible alone, not combined_mask."""
    target_angles = np.radians(np.array([10.0, 1.0, 0.5]))
    horizon_angles = np.radians(np.array([0.0, 0.0, 2.0]))

    terrain_visible = target_angles >= horizon_angles
    beam_within = (target_angles >= np.radians(-90.0)) & (target_angles <= np.radians(2.0))
    combined_mask = terrain_visible & beam_within

    # bin 0: terrain visible, but beam excluded (10° > 2° max) — NOT shadow
    assert terrain_visible[0] == True
    assert combined_mask[0] == False
    # bin 2: terrain blocked → goes to shadow regardless of beam
    assert terrain_visible[2] == False
    assert combined_mask[2] == False


# ---------------------------------------------------------------------------
# MapView._build_coverage_features — new dict format
# ---------------------------------------------------------------------------

def test_build_coverage_features_reads_outer_ring():
    """_build_coverage_features reads {"outer": [...], "inner": None} format."""
    _mv = _load("src/gui/map_view.py", "map_view")
    MapView = _mv.MapView
    view = object.__new__(MapView)   # bypass __init__ (no Qt needed for this method)
    view._coverage_opacity_factor = 1.0

    outer = [(51.0, 0.0), (51.1, 0.1), (51.0, 0.2), (50.9, 0.1)]
    coverage_data = {100.0: {"outer": outer, "inner": None}}

    features = view._build_coverage_features(coverage_data)

    assert len(features) == 1
    latlngs = features[0]["latlngs"]
    assert isinstance(latlngs, list)
    assert len(latlngs) == 1              # one ring, no donut
    assert isinstance(latlngs[0][0], list)   # [[lat, lon], ...]


def test_build_coverage_features_produces_two_rings_when_inner_given():
    """_build_coverage_features appends inner ring when inner coords provided."""
    _mv = _load("src/gui/map_view.py", "map_view")
    MapView = _mv.MapView
    view = object.__new__(MapView)
    view._coverage_opacity_factor = 1.0

    outer = [(51.0, 0.0), (51.1, 0.1), (51.0, 0.2), (50.9, 0.1)]
    inner = [(51.0, 0.01), (51.05, 0.05), (51.0, 0.09), (50.95, 0.05)]
    coverage_data = {100.0: {"outer": outer, "inner": inner}}

    features = view._build_coverage_features(coverage_data)

    assert len(features) == 1
    latlngs = features[0]["latlngs"]
    assert len(latlngs) == 2    # outer ring + inner ring (donut)
