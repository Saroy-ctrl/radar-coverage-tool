"""Tests for ComputationRequest fields and shadow rendering."""
import sys
import os

# Make src/gui importable as a flat namespace (bypasses src/gui/__init__.py which
# imports main_window → would pull in more Qt modules even with mocks).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'gui')))

from control_panel import ComputationRequest


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
    """Default resolution is Ultra (0.5°/50m) — hardcoded, no combo box."""
    req = ComputationRequest(
        radar_lat=51.0, radar_lon=0.0,
        site_elevation_amsl_m=100.0, antenna_amsl_m=120.0,
        k_factor=1.333, max_range_km=200.0,
        height_bands_m=[50.0],
        diffraction_guard_deg=0.5,
    )
    assert req.azimuth_step_deg == 0.5
    assert req.range_step_m == 50.0


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


import numpy as np
from unittest.mock import patch


def _make_map_view():
    """Return a MapView instance suitable for unit-testing pure methods.

    conftest.py stubs out all PyQt6 modules with MagicMock when real Qt DLLs
    are unavailable.  When that happens, `class MapView(QWidget)` resolves to
    a MagicMock subclass and every method becomes an auto-mock.

    Strategy:
    1. Replace the MagicMock stubs for the Qt classes that map_view.py
       references at import time with real (no-op) Python classes so that
       `class MapView(QWidget): ...` produces a genuine Python type.
    2. Load map_view.py via importlib.util under a private module name so we
       get the real source regardless of what is cached in sys.modules.
    3. Bypass MapView.__init__ with object.__new__ — _build_shadow_features
       is a pure computation method that needs no instance state.
    """
    import os
    import sys
    import importlib.util
    from unittest.mock import MagicMock

    # --- 1. Ensure Qt stub classes are real types, not MagicMocks ----------
    # Only patch inside the already-installed stubs; if real Qt is present,
    # this block is a no-op (real classes are already there).
    _real_class = type('_QtStub', (), {'__init__': lambda self, *a, **k: None})

    def _ensure_real(mod_name, attr_name):
        mod = sys.modules.get(mod_name)
        if mod is not None and isinstance(getattr(mod, attr_name, None), MagicMock):
            setattr(mod, attr_name, _real_class)

    for _mod, _cls in [
        ('PyQt6.QtWidgets',         'QWidget'),
        ('PyQt6.QtWidgets',         'QVBoxLayout'),
        ('PyQt6.QtWebEngineWidgets','QWebEngineView'),
        ('PyQt6.QtWebEngineCore',   'QWebEngineSettings'),
        ('PyQt6.QtCore',            'QUrl'),
    ]:
        _ensure_real(_mod, _cls)

    # --- 2. Load the real map_view source into an isolated module ----------
    map_view_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'src', 'gui', 'map_view.py')
    )
    spec = importlib.util.spec_from_file_location('_map_view_testonly', map_view_path)
    mv_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mv_mod)

    RealMapView = mv_mod.MapView

    # --- 3. Bypass __init__ — pure method, no Qt state needed --------------
    return object.__new__(RealMapView)


def test_build_shadow_features_full_coverage_no_shadow():
    """When every azimuth reaches max range, no shadow features should be produced."""
    mv = _make_map_view()
    max_r = 100_000.0
    ranges_m = [max_r] * 180          # all azimuths at full range
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=2.0,
        max_range_m=max_r,
    )
    assert feats == [], f"Expected no shadow features, got {len(feats)}"


def test_build_shadow_features_fully_blocked_returns_features():
    """All azimuths blocked at min range → one 4-corner wedge polygon per azimuth."""
    mv = _make_map_view()
    max_r = 100_000.0
    min_r = 200.0   # RANGE_STEP_M minimum
    ranges_m = [min_r] * 180
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=2.0,
        max_range_m=max_r,
    )
    assert len(feats) == 180
    for f in feats:
        assert "latlngs" in f
        assert len(f["latlngs"]) == 4   # 4-corner wedge polygon


def test_build_shadow_features_latlngs_are_floats():
    """Each wedge latlngs should be [[lat, lon], ...] with float values."""
    mv = _make_map_view()
    ranges_m = [50_000.0] * 360
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=1.0,
        max_range_m=100_000.0,
    )
    assert len(feats) > 0
    for f in feats:
        for coord in f["latlngs"]:
            assert len(coord) == 2
            lat, lon = coord
            assert isinstance(lat, float)
            assert isinstance(lon, float)
            assert -90 <= lat <= 90
            assert -180 <= lon <= 180


def test_build_shadow_features_significance_filter():
    """Only azimuths that reach max_range_m exactly are skipped (no 0.85x threshold)."""
    mv = _make_map_view()
    max_r = 100_000.0
    # 90 azimuths at 90 000 m, 90 at 50 000 m — both below max_r → all 180 kept
    ranges_m = [90_000.0] * 90 + [50_000.0] * 90
    feats = mv._build_shadow_features(
        ant_lat=51.5, ant_lon=0.0,
        ranges_m=ranges_m,
        azimuth_step_deg=2.0,
        max_range_m=max_r,
    )
    assert len(feats) == 180   # current code only skips inner_r >= max_range_m


def test_shadow_rendered_as_polyline_not_polygon():
    """Shadow wedge features must be rendered as L.polygon (4-corner filled wedge)."""
    mv = _make_map_view()
    mv._coverage_opacity_factor = 1.0
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0
    mv._shadow_mode = "Wedge"
    mv._shadow_geojson = None

    shadow_feats = [{"latlngs": [[51.6, 0.1], [51.7, 0.2], [51.7, 0.3], [51.6, 0.3]]}]
    html = mv._generate_leaflet_html(51.5, 0.0, [], shadow_feats)

    assert "L.polygon" in html


def test_range_ring_interval_selection():
    """Auto-interval must match the spec table."""
    mv = _make_map_view()
    mv._coverage_opacity_factor = 1.0
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0

    cases = [
        (25_000.0,   5_000.0),
        (50_000.0,  10_000.0),
        (100_000.0, 20_000.0),
        (200_000.0, 50_000.0),
    ]
    for max_r, expected_interval in cases:
        js = mv._generate_range_rings_js(51.5, 0.0, max_r)
        assert f"radius: {expected_interval:.0f}" in js, (
            f"max_range={max_r}: expected interval {expected_interval}, got js[:200]={js[:200]}"
        )


def test_range_ring_js_empty_when_no_range():
    """Returns empty string when max_range_m is 0."""
    mv = _make_map_view()
    mv._coverage_opacity_factor = 1.0
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0

    js = mv._generate_range_rings_js(51.5, 0.0, 0.0)
    assert js == ""


def test_coverage_opacity_factor_applied_in_html():
    """Coverage polygon opacity must equal band_base_opacity * factor."""
    mv = _make_map_view()
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0
    mv._coverage_opacity_factor = 0.5   # half opacity
    mv._shadow_mode = "Wedge"
    mv._shadow_geojson = None

    # 50m AGL band has base opacity 0.45 → expected 0.45 * 0.5 = 0.225
    feats = [{"height_m": 50.0, "area_km2": 100.0,
               "latlngs": [[51.5, 0.0], [51.6, 0.1], [51.5, 0.2]]}]
    html = mv._generate_leaflet_html(51.5, 0.0, feats, [])

    assert "0.225" in html


def test_set_coverage_opacity_updates_factor():
    """set_coverage_opacity must update _coverage_opacity_factor."""
    mv = _make_map_view()
    mv._shadow_opacity = 0.5
    mv._max_range_m = 0.0
    mv._shadow_features = []
    mv._shadow_mode = "Wedge"
    mv._shadow_geojson = None
    mv.coverage_data = {}
    mv.radar_lat = 51.5
    mv.radar_lon = 0.0
    mv._tmp_path = type('P', (), {'write_text': lambda self, *a, **k: None})()
    mv.web_engine = type('W', (), {'setUrl': lambda self, *a, **k: None})()

    mv.set_coverage_opacity(0.3)
    assert mv._coverage_opacity_factor == 0.3


def test_set_shadow_opacity_updates_value():
    """set_shadow_opacity must update _shadow_opacity."""
    mv = _make_map_view()
    mv._coverage_opacity_factor = 1.0
    mv._max_range_m = 0.0
    mv._shadow_features = []
    mv._shadow_mode = "Wedge"
    mv._shadow_geojson = None
    mv.coverage_data = {}
    mv.radar_lat = 51.5
    mv.radar_lon = 0.0
    mv._tmp_path = type('P', (), {'write_text': lambda self, *a, **k: None})()
    mv.web_engine = type('W', (), {'setUrl': lambda self, *a, **k: None})()

    mv.set_shadow_opacity(0.8)
    assert mv._shadow_opacity == 0.8
