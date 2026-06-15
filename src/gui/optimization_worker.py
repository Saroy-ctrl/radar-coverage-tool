"""
OptimizationWorker — QThread for grid-search top-K site ranking.

Encapsulates a background thread that evaluates candidate radar sites on a
geodesic grid and emits the top-K by total coverage area.

Signals:
- site_scored(i_done, total): after each candidate evaluation
- optimization_complete(top_k): emitted on success with [(lat, lon, score_km2), ...]
- optimization_error(error_msg): emitted on fatal exception
- progress_update(msg): status message (e.g., grid size)

Thread-safe cancel() method for graceful shutdown.
"""

import dataclasses
import numpy as np
import rasterio
from PyQt6.QtCore import QThread, pyqtSignal

from src.site_optimizer import generate_grid_points, compute_coverage_score
from src.gui.control_panel import ComputationRequest

# Scout resolution: ~100x faster than Ultra (0.5°/50m)
SCOUT_AZIMUTH_STEP = 5.0    # degrees
SCOUT_RANGE_STEP   = 500.0  # metres


class OptimizationWorker(QThread):
    """
    Grid-search top-K site optimizer for radar coverage.

    Spawns candidate sites across a bounding box, evaluates each using
    compute_coverage_score() with coarse scout resolution, and emits the
    top-K sites ranked by total coverage area.
    """

    site_scored           = pyqtSignal(int, int)   # (i_done, total)
    optimization_complete = pyqtSignal(list)       # [(lat, lon, score_km2), ...] best-first
    optimization_error    = pyqtSignal(str)
    progress_update       = pyqtSignal(str)

    def __init__(
        self,
        bbox: tuple,
        k: int,
        grid_step_m: int,
        request_template: ComputationRequest
    ):
        """
        Initialize the optimization worker.

        Args:
            bbox: (min_lat, min_lon, max_lat, max_lon) bounding box in WGS84
            k: number of top sites to return
            grid_step_m: candidate grid spacing in metres
            request_template: ComputationRequest from control panel;
                              radar_lat/radar_lon will be overridden per candidate
        """
        super().__init__()
        self.bbox = bbox
        self.k = k
        self.grid_step_m = grid_step_m
        self.request_template = request_template
        self._cancelled = False

    def cancel(self):
        """
        Thread-safe flag to cancel the optimization gracefully.

        The run() loop checks this at the top of each candidate iteration.
        """
        self._cancelled = True

    def run(self):
        """
        Main optimization loop (runs in background thread).

        1. Load DEM once (avoid N rasterio.open() calls for N candidates)
        2. Generate grid points over the bbox
        3. Evaluate each candidate with scout resolution
        4. Sort by coverage area (descending)
        5. Emit top-K or error
        """
        try:
            # Load DEM once — avoids N rasterio.open() calls (one per candidate)
            if not self.request_template.dem_path:
                self.optimization_error.emit("No DEM loaded. Load a DEM before running optimization.")
                return
            with rasterio.open(self.request_template.dem_path) as src:
                _dem_data = src.read(1).astype(np.float64)
                _dem_transform = src.transform
                _nodata = src.nodata
            if _nodata is not None:
                _dem_data = np.where(_dem_data == _nodata, 0.0, _dem_data)
            _dem_data = np.where((_dem_data < -500) | (_dem_data > 9000), 0.0, _dem_data)

            # Generate candidate sites across the bounding box
            points = generate_grid_points(*self.bbox, self.grid_step_m)
            total = len(points)
            self.progress_update.emit(f"Evaluating {total} candidate sites...")

            scores = []
            for i, (lat, lon) in enumerate(points):
                # Check cancellation flag
                if self._cancelled:
                    return

                # Create a request for this candidate with scout resolution
                req = dataclasses.replace(
                    self.request_template,
                    radar_lat=lat,
                    radar_lon=lon,
                    azimuth_step_deg=SCOUT_AZIMUTH_STEP,
                    range_step_m=SCOUT_RANGE_STEP,
                )

                # Evaluate this candidate; skip silently on exception
                try:
                    score = compute_coverage_score(req, dem_data=_dem_data, dem_transform=_dem_transform)
                    scores.append((lat, lon, score))
                except Exception:
                    pass

                # Emit progress
                self.site_scored.emit(i + 1, total)

            # Sort by score descending and take top-K
            top_k = sorted(scores, key=lambda x: -x[2])[: self.k]
            self.optimization_complete.emit(top_k)

        except Exception as exc:
            import traceback
            error_msg = f"Optimization error: {exc}\n{traceback.format_exc()}"
            self.optimization_error.emit(error_msg)
