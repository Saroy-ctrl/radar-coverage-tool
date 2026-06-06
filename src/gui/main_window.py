"""
Main Window — QMainWindow with dark theme, layout orchestration, and QThread worker.

Responsibilities:
- Dark theme (#1e1e2e background)
- Split layout: left=control_panel, center/right=map+polar views
- QThread worker for background computation
- Signal coordination between control_panel → worker → views
- Status bar: antenna height, coverage area, void %
- Menu bar: File (Load DEM, Export), View (theme toggle)
- Never blocks main thread during computation
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QHBoxLayout,
    QLabel, QProgressBar, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QAction

from src.gui.control_panel import ControlPanel, ComputationRequest
from src.gui.map_view import MapView
from src.gui.polar_view import PolarView
from src.shadow_builder import extract_blocked_segments


# Dark theme colors
DARK_BG = "#1e1e2e"
DARK_PANEL = "#2a2a3e"
ACCENT_BLUE = "#4a9eff"


class ComputationWorker(QThread):
    """
    Background worker thread for coverage computation.
    Emits signals for progress and results without blocking main thread.
    """

    # Signals
    progress_update = pyqtSignal(str)  # Status message
    coverage_computed = pyqtSignal(dict)  # {height: polygon_coords, ...}
    polar_data_ready = pyqtSignal(dict)  # Polar plot data
    error_occurred = pyqtSignal(str)  # Error message
    computation_finished = pyqtSignal()
    shadow_data_ready = pyqtSignal(dict)    # blocking range per azimuth for lowest height band
    computation_timed = pyqtSignal(float)   # elapsed wall-clock seconds for this run

    def __init__(self, request: ComputationRequest):
        super().__init__()
        self.request = request
        self._is_running = True

    def run(self):
        """Execute computation in background thread using real backend engines."""
        try:
            import time as _time
            import numpy as np
            import rasterio
            from pyproj import Geod
            from src.earth_model import EarthModel
            from src.visibility_engine import VisibilityEngine
            from src.obstruction_engine import ObstructionEngine

            GEOD = Geod(ellps='WGS84')
            req = self.request

            if not req.dem_path:
                self.error_occurred.emit("No DEM loaded. Use 'Browse DEM...' to load a GeoTIFF file.")
                return

            self.progress_update.emit("Loading DEM...")

            # Load DEM into memory (float64, nodata → 0)
            with rasterio.open(req.dem_path) as src:
                dem_data = src.read(1).astype(np.float64)
                transform = src.transform
                nodata = src.nodata
                dem_bounds = src.bounds   # (left, bottom, right, top)

            if nodata is not None:
                dem_data = np.where(dem_data == nodata, 0.0, dem_data)
            dem_data = np.where((dem_data < -500) | (dem_data > 9000), 0.0, dem_data)

            self.progress_update.emit(
                f"DEM loaded: {dem_data.shape[1]}×{dem_data.shape[0]} px, "
                f"bounds W{dem_bounds.left:.2f} E{dem_bounds.right:.2f} "
                f"S{dem_bounds.bottom:.2f} N{dem_bounds.top:.2f}"
            )

            # Vectorised DEM sampler: (lats_array, lons_array) → elevations
            # Returns 0 for points outside the DEM extent.
            def sample_dem(lats, lons):
                # rasterio affine: col = (lon - x_origin) / pixel_width
                #                   row = (lat - y_origin) / pixel_height  (pixel_height < 0)
                cols = (lons - transform.c) / transform.a
                rows = (lats - transform.f) / transform.e

                in_bounds = (
                    (rows >= 0) & (rows < dem_data.shape[0]) &
                    (cols >= 0) & (cols < dem_data.shape[1])
                )

                rows_c = np.clip(np.round(rows).astype(int), 0, dem_data.shape[0] - 1)
                cols_c = np.clip(np.round(cols).astype(int), 0, dem_data.shape[1] - 1)

                result = dem_data[rows_c, cols_c]
                # Outside DEM: return 0 (sea level) rather than a clipped boundary value
                result = np.where(in_bounds, result, 0.0)
                return result

            # Setup physics engines
            earth = EarthModel(k_factor=req.k_factor)
            vis = VisibilityEngine(
                earth_model_instance=earth,
                diffraction_guard_rad=np.radians(req.diffraction_guard_deg)
            )

            # Load obstructions if provided
            obs_engine = ObstructionEngine()
            obstructions_df = None
            if req.obstructions_path:
                try:
                    obstructions_df = obs_engine.load_obstructions(req.obstructions_path)
                    self.progress_update.emit(f"Obstructions loaded: {len(obstructions_df)} features")
                except Exception as e:
                    self.progress_update.emit(f"Warning: could not load obstructions: {e}")

            ant_lat = req.radar_lat
            ant_lon = req.radar_lon
            antenna_amsl_m = req.antenna_amsl_m
            site_elev_m = req.site_elevation_amsl_m
            max_range_m = req.max_range_km * 1000.0

            # ---------------------------------------------------------------
            # CRITICAL: range bins must START at 0 so that the first entry in
            # the profile is the antenna position itself.  VisibilityEngine
            # computes horizon angles as atan2(h_terrain - h_antenna, range).
            # If range[0] == 0 the antenna bin is skipped (arctan2(0,0)=0) and
            # accumulation works correctly. Starting at RANGE_STEP_M means the
            # very first bin is already at range>0, which can produce a spurious
            # blocking angle and collapse coverage to a uniform circle.
            # ---------------------------------------------------------------
            RANGE_STEP_M = req.range_step_m
            AZIMUTH_STEP = req.azimuth_step_deg

            azimuths = np.arange(0, 360, AZIMUTH_STEP, dtype=np.float64)
            n_az = len(azimuths)

            # Profile ranges: 0 (antenna) then every RANGE_STEP_M out to max
            ranges = np.arange(0.0, max_range_m + RANGE_STEP_M, RANGE_STEP_M,
                               dtype=np.float64)

            # height_bands_m are AGL → target AMSL = site_elevation + height_agl
            heights_agl = req.height_bands_m
            coverage_ranges_m = {h: np.zeros(n_az, dtype=np.float64) for h in heights_agl}

            _min_h_agl = min(heights_agl) if heights_agl else None
            all_shadow_segments = []   # list of (az_deg, inner_r_m, outer_r_m)

            # Store per-azimuth horizon angles for polar plot
            horizon_angles_deg = np.zeros(n_az, dtype=np.float64)

            self.progress_update.emit("Computing coverage (this may take a minute)...")
            _t_start = _time.monotonic()

            for i, az in enumerate(azimuths):
                # Forward geodetic: all range bins at once
                lons_p, lats_p, _ = GEOD.fwd(
                    np.full(len(ranges), ant_lon),
                    np.full(len(ranges), ant_lat),
                    np.full(len(ranges), az),
                    ranges
                )
                # Antenna bin (range=0): fix to exact antenna position
                lats_p[0] = ant_lat
                lons_p[0] = ant_lon

                elevations = sample_dem(np.asarray(lats_p), np.asarray(lons_p))
                # Antenna bin: use the known site elevation, not a DEM sample
                elevations[0] = site_elev_m

                profile = {
                    'range_m': ranges,
                    'elevation_amsl_m': elevations
                }

                # Inject obstructions for this azimuth
                if obstructions_df is not None:
                    try:
                        profile = obs_engine.inject_obstructions(
                            profile, ant_lat, ant_lon, obstructions_df=obstructions_df
                        )
                    except Exception:
                        pass

                # ── Horizon computation (once per azimuth, reused for all heights) ─────
                # horizon_result['horizon_angle_rad'][i] = cumulative max obstruction angle
                # horizon_result['h_apparent_m'][i]      = terrain[i] - curvature[i]
                horizon_result = vis.compute_horizon_angles(profile, antenna_amsl_m)
                horizon_angles = horizon_result['horizon_angle_rad']
                h_apparent     = horizon_result['h_apparent_m']   # shape: (n_ranges,)

                # Polar plot: record max horizon obstruction angle for this azimuth
                horizon_angles_deg[i] = np.degrees(float(np.max(horizon_angles)))

                # ── Per-height AGL coverage ──────────────────────────────────────────
                # Correct AGL semantics: target flies h_agl metres above LOCAL terrain.
                #   target_amsl[j]     = elevations[j] + h_agl
                #   target_apparent[j] = target_amsl[j] - curvature[j]
                #                      = h_apparent[j] + h_agl
                #   target_angle[j]    = arctan2(target_apparent[j] - antenna_amsl, ranges[j])
                #
                # Terrain affects the HORIZON (blocking angle) but cancels in target_apparent,
                # so the target angle becomes arctan2(h_apparent[j] + h_agl - antenna_amsl, r[j]).
                # Coverage is cut where terrain horizon angle exceeds this decreasing target angle.

                for h_agl in heights_agl:
                    # Per-range target elevation angles
                    target_angles = np.arctan2(
                        h_apparent + h_agl - antenna_amsl_m,
                        ranges
                    )

                    # Visible where target angle >= cumulative horizon (skip bin 0 = antenna)
                    visible_mask = target_angles[1:] >= horizon_angles[1:]
                    visible_idx  = np.where(visible_mask)[0]

                    # Use LAST visible bin as coverage range (outer boundary).
                    # First-blocked gives the near-range mountain shadow (e.g. Teide
                    # slope at 5 km), not the outer coverage limit (ocean at 79 km).
                    if len(visible_idx) > 0:
                        max_r = float(ranges[1:][visible_idx[-1]])
                    else:
                        max_r = float(ranges[1])   # nothing visible: one step

                    coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)

                    if h_agl == _min_h_agl:
                        segs = extract_blocked_segments(visible_mask, ranges, az)
                        all_shadow_segments.extend(segs)

                if i % 20 == 0:
                    pct = int(i / n_az * 100)
                    self.progress_update.emit(f"Computing... {pct}%")

            _elapsed = _time.monotonic() - _t_start

            self.progress_update.emit("Building coverage polygons...")
            self.computation_timed.emit(_elapsed)

            # Shadow data for MapView: both modes use the same payload.
            # "ranges_m" drives the fast wedge mode.
            # "shadow_segments" drives the smooth polygon mode.
            if heights_agl:
                _lowest_h = min(heights_agl)
                shadow_payload = {
                    "ranges_m":        coverage_ranges_m[_lowest_h].tolist(),
                    "shadow_segments": all_shadow_segments,
                    "azimuth_step_deg": float(AZIMUTH_STEP),
                    "max_range_m":      float(max_range_m),
                }
                self.shadow_data_ready.emit(shadow_payload)

            # Polar data: ranges in km
            polar_ranges = {}
            for h in heights_agl:
                polar_ranges[h] = (coverage_ranges_m[h] / 1000.0).tolist()

            polar_data = {
                "azimuths": azimuths.tolist(),
                "ranges": polar_ranges,
                # Horizon as maximum obstruction elevation angle per azimuth (degrees)
                # Converted to a pseudo-range so it shows on the polar r-axis.
                # We pass it as-is and let PolarView decide how to render it.
                "horizon": horizon_angles_deg.tolist(),
            }

            # Map coverage data: {height_m: [(lat, lon), ...]} for each height band
            # Build a closed polygon ring using GEOD.fwd to turn (azimuth, range) → (lat, lon)
            coverage_data = {}
            for h in heights_agl:
                ranges_h = coverage_ranges_m[h]
                # Replace any zero-range azimuths with a tiny offset so the polygon isn't degenerate
                ranges_h = np.where(ranges_h < RANGE_STEP_M, RANGE_STEP_M, ranges_h)

                lons_p, lats_p, _ = GEOD.fwd(
                    np.full(n_az, ant_lon),
                    np.full(n_az, ant_lat),
                    azimuths,
                    ranges_h
                )
                # Wrap around: first point appended to close the ring
                lat_list = lats_p.tolist() + [lats_p[0]]
                lon_list = lons_p.tolist() + [lons_p[0]]
                coverage_data[h] = list(zip(lat_list, lon_list))

            self.coverage_computed.emit(coverage_data)
            self.polar_data_ready.emit(polar_data)
            self.progress_update.emit("Computation complete")
            self.computation_finished.emit()

        except Exception as e:
            import traceback
            self.error_occurred.emit(f"Computation error: {str(e)}\n{traceback.format_exc()}")


class MainWindow(QMainWindow):
    """
    Top-level application window.
    Orchestrates layout, menu, status bar, and background computation.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Radar Coverage Analysis Tool")
        self.setGeometry(100, 100, 1600, 900)

        # Apply dark theme
        self._apply_dark_theme()

        # Create GUI components
        self._create_widgets()
        self._create_menu_bar()
        self._create_status_bar()
        self._create_layout()

        # State
        self.computation_worker = None
        self.computation_request = None
        self._last_coverage_data = {}

    def _apply_dark_theme(self):
        """Set dark theme stylesheet."""
        dark_stylesheet = f"""
            QMainWindow {{
                background-color: {DARK_BG};
                color: #e8e8e8;
            }}
            QWidget {{
                background-color: {DARK_BG};
                color: #e8e8e8;
            }}
            QMenuBar {{
                background-color: {DARK_PANEL};
                color: #e8e8e8;
                border-bottom: 1px solid #444;
            }}
            QMenuBar::item:selected {{
                background-color: {ACCENT_BLUE};
                color: white;
            }}
            QMenu {{
                background-color: {DARK_PANEL};
                color: #e8e8e8;
                border: 1px solid #444;
            }}
            QMenu::item:selected {{
                background-color: {ACCENT_BLUE};
                color: white;
            }}
            QStatusBar {{
                background-color: {DARK_PANEL};
                color: #e8e8e8;
                border-top: 1px solid #444;
            }}
            QPushButton {{
                background-color: {ACCENT_BLUE};
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 3px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #6bb5ff;
            }}
            QPushButton:pressed {{
                background-color: #2b8fe0;
            }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
                background-color: #3a3a4e;
                color: #e8e8e8;
                border: 1px solid #555;
                padding: 4px;
                border-radius: 3px;
            }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border: 2px solid {ACCENT_BLUE};
            }}
            QGroupBox {{
                color: #e8e8e8;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 8px;
                padding-top: 8px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 3px 0 3px;
            }}
            QLabel {{
                color: #e8e8e8;
            }}
            QProgressBar {{
                background-color: #3a3a4e;
                border: 1px solid #555;
                border-radius: 3px;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: {ACCENT_BLUE};
            }}
            QCheckBox, QRadioButton {{
                color: #e8e8e8;
                spacing: 4px;
            }}
            QCheckBox::indicator, QRadioButton::indicator {{
                width: 14px;
                height: 14px;
            }}
            QCheckBox::indicator:unchecked {{
                background-color: #3a3a4e;
                border: 1px solid #555;
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT_BLUE};
                border: 1px solid {ACCENT_BLUE};
            }}
            QTableWidget {{
                background-color: #2a2a3e;
                alternate-background-color: #3a3a4e;
                color: #e8e8e8;
                border: 1px solid #555;
            }}
            QTableWidget::item {{
                padding: 4px;
            }}
            QTableWidget::item:selected {{
                background-color: {ACCENT_BLUE};
            }}
            QHeaderView::section {{
                background-color: {DARK_PANEL};
                color: #e8e8e8;
                padding: 4px;
                border: 1px solid #555;
            }}
        """
        self.setStyleSheet(dark_stylesheet)

    def _create_widgets(self):
        """Create main panel widgets."""
        self.control_panel = ControlPanel()
        self.map_view = MapView()
        self.polar_view = PolarView()

    def _create_layout(self):
        """Create main layout: left=control, right=map+polar (vertical split)."""
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        # Main horizontal splitter: control panel | (map + polar)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {DARK_PANEL}; }}")

        # Left: Control panel
        main_splitter.addWidget(self.control_panel)
        self.control_panel.setMaximumWidth(350)

        # Right: Vertical splitter for map and polar
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self.map_view)
        right_splitter.addWidget(self.polar_view)
        right_splitter.setStretchFactor(0, 70)
        right_splitter.setStretchFactor(1, 30)

        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 0)  # Control panel: fixed
        main_splitter.setStretchFactor(1, 1)  # Right area: expandable

        # Central layout
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(main_splitter)

        # Connect control panel signals
        self.control_panel.compute_requested.connect(self._on_compute_requested)
        self.control_panel.load_dem_requested.connect(self._on_load_dem)
        self.control_panel.load_obstructions_requested.connect(self._on_load_obstructions)
        self.control_panel.export_geojson_requested.connect(self._on_export_geojson)
        self.control_panel.coverage_opacity_changed.connect(self.map_view.set_coverage_opacity)
        self.control_panel.shadow_opacity_changed.connect(self.map_view.set_shadow_opacity)

    def _create_menu_bar(self):
        """Create menu bar with File and View menus."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        load_dem_action = QAction("Load DEM...", self)
        load_dem_action.triggered.connect(self._on_load_dem)
        file_menu.addAction(load_dem_action)

        load_obstructions_action = QAction("Load Obstructions...", self)
        load_obstructions_action.triggered.connect(self._on_load_obstructions)
        file_menu.addAction(load_obstructions_action)

        file_menu.addSeparator()

        export_geojson_action = QAction("Export Coverage (GeoJSON)...", self)
        export_geojson_action.triggered.connect(self._on_export_geojson)
        file_menu.addAction(export_geojson_action)

        export_png_action = QAction("Export Polar Diagram (PNG)...", self)
        export_png_action.triggered.connect(self._on_export_png)
        file_menu.addAction(export_png_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("View")

        theme_action = QAction("Toggle Dark/Light Theme", self)
        theme_action.triggered.connect(self._on_toggle_theme)
        view_menu.addAction(theme_action)

    def _create_status_bar(self):
        """Create status bar with computation info."""
        self.status_bar = self.statusBar()

        # Antenna height label
        self.label_antenna_height = QLabel("Antenna: 0 m AMSL")
        self.status_bar.addWidget(self.label_antenna_height)

        # Coverage area label
        self.label_coverage_area = QLabel("Coverage area: — km²")
        self.status_bar.addWidget(self.label_coverage_area)

        # Void percentage label
        self.label_void_pct = QLabel("Void fill: — %")
        self.status_bar.addWidget(self.label_void_pct)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addWidget(self.progress_bar)

        # Status message (right-aligned)
        self.label_status = QLabel("Ready")
        self.status_bar.addPermanentWidget(self.label_status)

    def _on_compute_requested(self, request: ComputationRequest):
        """Handle compute button from control panel."""
        if self.computation_worker is not None and self.computation_worker.isRunning():
            QMessageBox.warning(self, "Computation in Progress",
                              "A computation is already running. Please wait.")
            return

        self.computation_request = request
        self.label_status.setText("Computing...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Update map to antenna location before computing
        self.map_view.set_antenna_location(request.radar_lat, request.radar_lon)
        self.label_antenna_height.setText(f"Antenna: {request.antenna_amsl_m:.0f} m AMSL")

        # Start worker thread
        self.computation_worker = ComputationWorker(request)
        self.computation_worker.progress_update.connect(self._on_progress_update)
        self.computation_worker.coverage_computed.connect(self._on_coverage_computed)
        self.computation_worker.polar_data_ready.connect(self._on_polar_data_ready)
        self.computation_worker.error_occurred.connect(self._on_computation_error)
        self.computation_worker.computation_finished.connect(self._on_computation_finished)
        self.computation_worker.shadow_data_ready.connect(self._on_shadow_data_ready)
        self.computation_worker.computation_timed.connect(self._on_computation_timed)
        self.computation_worker.start()

    def _on_progress_update(self, message: str):
        """Update progress label from worker."""
        self.label_status.setText(message)
        # Animate progress bar for visual feedback
        current = self.progress_bar.value()
        if current < 90:
            self.progress_bar.setValue(current + 10)

    def _on_coverage_computed(self, coverage_data: dict):
        """Receive coverage polygons from worker."""
        self._last_coverage_data = coverage_data
        self.map_view.update_coverage(coverage_data)

        # Compute area of largest polygon (highest coverage height)
        if coverage_data:
            try:
                from pyproj import Geod
                from shapely.geometry import Polygon
                geod = Geod(ellps='WGS84')
                largest_h = max(coverage_data.keys())
                coords = coverage_data[largest_h]
                if len(coords) >= 3:
                    # shapely Polygon takes (lon, lat); coords are (lat, lon)
                    poly = Polygon([(c[1], c[0]) for c in coords])
                    area_m2, _ = geod.geometry_area_perimeter(poly)
                    area_km2 = abs(area_m2) / 1e6
                    self.label_coverage_area.setText(f"Coverage area: {area_km2:.0f} km²")
                else:
                    self.label_coverage_area.setText("Coverage area: — km²")
            except Exception:
                self.label_coverage_area.setText("Coverage area: — km²")

    def _on_polar_data_ready(self, polar_data: dict):
        """Receive polar diagram data from worker."""
        self.polar_view.update_data(polar_data)

    def _on_shadow_data_ready(self, payload: dict):
        """Forward shadow blocking data to MapView."""
        req = self.computation_request
        if req is None:
            return
        self.map_view.set_shadow_data(req.radar_lat, req.radar_lon, payload)

    def _on_computation_timed(self, elapsed_seconds: float):
        """Recalibrate control panel estimate label with measured time."""
        self.control_panel.recalibrate_estimate(elapsed_seconds)

    def _on_computation_error(self, error_msg: str):
        """Handle computation error."""
        self.label_status.setText("Error")
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "Computation Error", error_msg)

    def _on_computation_finished(self):
        """Finalize computation."""
        self.progress_bar.setValue(100)
        self.label_status.setText("Ready")
        self.control_panel.set_computing_finished()
        QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False))

    def _on_load_dem(self):
        """Open file dialog to load DEM file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load DEM File", "",
            "GeoTIFF (*.tif *.tiff);;All Files (*)"
        )
        if path:
            self.control_panel.set_dem_path(path)
            self.label_status.setText(f"DEM loaded: {Path(path).name}")

    def _on_load_obstructions(self):
        """Open file dialog to load obstructions CSV."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Obstructions CSV", "",
            "CSV (*.csv);;All Files (*)"
        )
        if path:
            self.control_panel.set_obstructions_path(path)
            self.label_status.setText(f"Obstructions loaded: {Path(path).name}")

    def _on_export_geojson(self):
        """Export coverage as GeoJSON."""
        if not self._last_coverage_data:
            QMessageBox.information(self, "No Data", "Compute coverage first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Coverage as GeoJSON", "",
            "GeoJSON (*.geojson);;All Files (*)"
        )
        if path:
            try:
                import json
                from src.coverage_engine import CoverageEngine
                engine = CoverageEngine()

                # Build a merged FeatureCollection from all height bands
                all_geojsons = []
                for height_m, coords in self._last_coverage_data.items():
                    if len(coords) < 3:
                        continue
                    lats = [c[0] for c in coords]
                    lons = [c[1] for c in coords]
                    import numpy as np
                    gj = engine.polar_to_geojson(
                        self.computation_request.radar_lat,
                        self.computation_request.radar_lon,
                        np.linspace(0, 360, len(lats), endpoint=False),
                        np.array([6371000.0] * len(lats)),  # placeholder
                        height_m=height_m
                    )
                    all_geojsons.append(gj)

                merged = engine.geojson_to_feature_collection(*all_geojsons)
                engine.export_geojson(merged, path)
                self.label_status.setText(f"Exported: {Path(path).name}")
                QMessageBox.information(self, "Export", f"Saved to {Path(path).name}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def _on_export_png(self):
        """Export polar diagram as PNG."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Polar Diagram as PNG", "",
            "PNG (*.png);;All Files (*)"
        )
        if path:
            ok = self.polar_view.save_as_png(path)
            if ok:
                self.label_status.setText(f"Exported: {Path(path).name}")
                QMessageBox.information(self, "Export", f"Saved to {Path(path).name}")
            else:
                QMessageBox.warning(self, "Export Failed", "Could not save PNG.")

    def _on_toggle_theme(self):
        """Toggle dark/light theme (stub for future)."""
        QMessageBox.information(self, "Theme Toggle", "Light theme not yet implemented.")


def main():
    """Application entry point."""
    app = __import__('PyQt6.QtWidgets', fromlist=['QApplication']).QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
