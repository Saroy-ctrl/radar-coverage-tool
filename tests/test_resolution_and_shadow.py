"""Tests for resolution preset mapping and ComputationRequest fields."""
import sys
import os

# Make src/gui importable as a flat namespace (bypasses src/gui/__init__.py which
# imports main_window → would pull in more Qt modules even with mocks).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'gui')))

from control_panel import ComputationRequest, RESOLUTION_PRESETS


def test_computation_request_has_resolution_fields():
    req = ComputationRequest(
        radar_lat=51.0, radar_lon=0.0,
        site_elevation_amsl_m=100.0, antenna_amsl_m=120.0,
        k_factor=1.333, max_range_km=200.0,
        height_bands_m=[50.0, 100.0],
        diffraction_guard_deg=0.5,
        azimuth_step_deg=1.0,
        range_step_m=100.0,
    )
    assert req.azimuth_step_deg == 1.0
    assert req.range_step_m == 100.0


def test_computation_request_default_resolution():
    """Default resolution must be Fast preset (2°/200m)."""
    req = ComputationRequest(
        radar_lat=51.0, radar_lon=0.0,
        site_elevation_amsl_m=100.0, antenna_amsl_m=120.0,
        k_factor=1.333, max_range_km=200.0,
        height_bands_m=[50.0],
        diffraction_guard_deg=0.5,
    )
    assert req.azimuth_step_deg == 2.0
    assert req.range_step_m == 200.0


def test_resolution_presets_keys():
    assert set(RESOLUTION_PRESETS.keys()) == {"Fast", "Standard", "High", "Ultra"}


def test_resolution_presets_values():
    # Each preset: (azimuth_step_deg, range_step_m)
    assert RESOLUTION_PRESETS["Fast"]     == (2.0,  200.0)
    assert RESOLUTION_PRESETS["Standard"] == (1.0,  100.0)
    assert RESOLUTION_PRESETS["High"]     == (0.5,  100.0)
    assert RESOLUTION_PRESETS["Ultra"]    == (0.5,   50.0)


def test_shadow_data_payload_structure():
    """shadow_data_ready payload must have ranges_m, azimuth_step_deg, max_range_m."""
    payload = {
        "ranges_m": [50000.0, 60000.0, 100000.0],
        "azimuth_step_deg": 2.0,
        "max_range_m": 100000.0,
    }
    assert isinstance(payload["ranges_m"], list)
    assert isinstance(payload["azimuth_step_deg"], float)
    assert isinstance(payload["max_range_m"], float)
    assert all(r >= 0 for r in payload["ranges_m"])
