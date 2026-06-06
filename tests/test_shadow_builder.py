"""
Tests for src/shadow_builder.py::extract_blocked_segments.

These tests verify the run-length encoding of blocked range bins and correct
handling of edge cases (clamping, single-bin advancement, multi-run scenarios).
"""

import numpy as np
import pytest
from src.shadow_builder import extract_blocked_segments


def test_flat_terrain_no_segments():
    """All visible → no shadow segments."""
    ranges = np.arange(0.0, 50100.0, 100.0)       # 0..50000, step 100m
    visible = np.ones(len(ranges) - 1, dtype=bool)
    segs = extract_blocked_segments(visible, ranges, az_deg=0.0)
    assert segs == []


def test_all_blocked_one_segment():
    """All blocked → one segment spanning full range."""
    ranges = np.arange(0.0, 10100.0, 100.0)        # 0..10000, step 100m
    visible = np.zeros(len(ranges) - 1, dtype=bool)
    segs = extract_blocked_segments(visible, ranges, az_deg=90.0)
    assert len(segs) == 1
    az, inner_r, outer_r = segs[0]
    assert az == 90.0
    assert inner_r == 200.0       # clamped to MIN_INNER_R_M
    assert outer_r == ranges[-1]  # 10000 m


def test_single_ridge_two_segments():
    """
    Two blocked regions separated by a visible gap.
    visible_mask has:
      - indices 0-9: True (10 visible bins)
      - indices 10-20: False (11 blocked bins)
      - indices 21-39: True (19 visible bins)
      - indices 40-50: False (11 blocked bins)
      - indices 51+: True (remainder visible)
    """
    step = 100.0
    n = 100
    ranges = np.arange(0.0, (n + 1) * step, step)    # 0..10000, 101 points
    visible = np.ones(n, dtype=bool)
    visible[10:21] = False   # blocked from bin 10 to bin 20 inclusive (11 bins)
    visible[40:51] = False   # blocked from bin 40 to bin 50 inclusive (11 bins)

    segs = extract_blocked_segments(visible, ranges, az_deg=45.0)
    assert len(segs) == 2

    # First blocked run: indices 10-20 (s=10, e=21)
    # inner_r = ranges[s+1] = ranges[11] = 1100 m
    # outer_r = ranges[e] = ranges[21] = 2100 m
    az0, in0, out0 = segs[0]
    assert az0 == 45.0
    assert in0 == pytest.approx(1100.0)
    assert out0 == pytest.approx(2100.0)

    # Second blocked run: indices 40-50 (s=40, e=51)
    # inner_r = ranges[s+1] = ranges[41] = 4100 m
    # outer_r = ranges[e] = ranges[51] = 5100 m
    az1, in1, out1 = segs[1]
    assert az1 == 45.0
    assert in1 == pytest.approx(4100.0)
    assert out1 == pytest.approx(5100.0)


def test_inner_r_clamped_to_minimum():
    """Blocked bins starting near antenna → inner_r clamped to MIN_INNER_R_M."""
    step = 50.0
    ranges = np.arange(0.0, 5050.0, step)         # 0..5000, ranges[1]=50 m < MIN_INNER_R_M
    visible = np.ones(len(ranges) - 1, dtype=bool)
    visible[0:5] = False                           # blocked from indices 0-4

    segs = extract_blocked_segments(visible, ranges, az_deg=180.0)
    assert len(segs) == 1
    az, inner_r, outer_r = segs[0]
    assert az == 180.0
    # inner_r = max(ranges[1], 200) = max(50, 200) = 200 m
    assert inner_r == 200.0
    # outer_r = ranges[5] = 250 m
    assert outer_r == pytest.approx(250.0)
