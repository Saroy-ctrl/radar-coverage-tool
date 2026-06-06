# tests/test_dem_manager.py
"""
Tests for DEM tile manager — tile name convention and find_required_tiles.
These tests do NOT require real DEM files on disk.
"""
import pytest
from src.dem_manager import _srtm_tile_name, find_required_tiles


class TestSrtmTileName:
    def test_north_east(self):
        assert _srtm_tile_name(28, 14) == "N28E014.tif"

    def test_north_west(self):
        assert _srtm_tile_name(51, -1) == "N51W001.tif"

    def test_south_east(self):
        assert _srtm_tile_name(-1, 36) == "S01E036.tif"

    def test_south_west(self):
        assert _srtm_tile_name(-34, -70) == "S34W070.tif"

    def test_equator_prime_meridian(self):
        assert _srtm_tile_name(0, 0) == "N00E000.tif"


class TestFindRequiredTiles:
    def test_small_range_returns_at_least_one_tile(self):
        # 10 km range — must include the tile the radar sits in
        tiles = find_required_tiles(28.27, -16.64, max_range_km=10)
        assert "N28W017.tif" in tiles

    def test_large_range_covers_neighbors(self):
        # 200 km from Tenerife spans multiple 1° tiles
        tiles = find_required_tiles(28.27, -16.64, max_range_km=200)
        # Must span at least 3 lat rows × 3 lon columns
        assert len(tiles) >= 9

    def test_tile_names_follow_srtm_convention(self):
        tiles = find_required_tiles(51.5, 1.3, max_range_km=50)
        for name in tiles:
            assert name.endswith(".tif")
            # First char N or S, then 2 digits, then E or W, then 3 digits
            assert name[0] in ('N', 'S')
            assert name[3] in ('E', 'W')
            assert name[1:3].isdigit()
            assert name[4:7].isdigit()
