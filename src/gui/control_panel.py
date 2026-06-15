"""
Control Panel — All input widgets for radar parameters.

Responsibilities:
- Radar antenna location: lat/lon spinboxes (WGS84, ±0.0001°)
- Antenna AMSL height: spinbox (0–10000m)
- K-factor slider + display (0.6–1.5, default 4/3 ≈ 1.333)
- Max instrumented range: spinbox (0–500km)
- Height band selection: checkboxes for [50, 100, 500, 1000, 3000m]
- Diffraction guard: slider (0–2°, default 0.5°)
- Compute button + "Computing..." indicator
- Load DEM, Load Obstructions CSV, Export Coverage buttons
- Signal: compute_requested(ComputationRequest) → main_window
"""

from dataclasses import dataclass
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QSpinBox, QDoubleSpinBox,
    QSlider, QPushButton, QCheckBox, QFileDialog,
    QProgressBar, QComboBox, QLineEdit, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor


# Default height bands with Cambridge Pixel colors
HEIGHT_BANDS = {
    50: {"color": "#00cc44", "name": "50m (low)"},
    100: {"color": "#aacc00", "name": "100m (low-mid)"},
    500: {"color": "#ff8800", "name": "500m (mid)"},
    1000: {"color": "#ff3300", "name": "1000m (high)"},
    3000: {"color": "#cc00ff", "name": "3000m (very high)"},
}



@dataclass
class ComputationRequest:
    """Data class passed to compute signal."""
    radar_lat: float
    radar_lon: float
    site_elevation_amsl_m: float
    antenna_amsl_m: float
    k_factor: float
    max_range_km: float
    height_bands_m: list[float]
    diffraction_guard_deg: float
    dem_path: str = None
    obstructions_path: str = None
    azimuth_step_deg: float = 0.5
    range_step_m: float = 50.0
    min_beam_deg: float = -90.0
    max_beam_deg: float = 90.0


class ControlPanel(QWidget):
    """Control panel with all radar parameter inputs."""

    # Signals
    compute_requested = pyqtSignal(ComputationRequest)
    load_dem_requested = pyqtSignal()
    load_obstructions_requested = pyqtSignal()
    export_geojson_requested = pyqtSignal()
    coverage_opacity_changed = pyqtSignal(float)
    shadow_opacity_changed = pyqtSignal(float)
    shadow_mode_changed = pyqtSignal(str)

    # Top-K Site Finder signals
    find_top_k_requested = pyqtSignal(float, float, float, float, int, int)
    #                                  min_lat min_lon max_lat max_lon k step_m
    # PyQt6 signals cannot carry tuple — bbox passed as 4 individual floats.
    draw_bbox_requested   = pyqtSignal()
    cancel_optimization   = pyqtSignal()
    load_site_requested   = pyqtSignal(float, float)   # (lat, lon)
    bbox_changed          = pyqtSignal(float, float, float, float)  # min_lat,min_lon,max_lat,max_lon

    def __init__(self):
        super().__init__()
        self.dem_path = None
        self.obstructions_path = None
        self.is_computing = False

        self._create_widgets()
        self._create_layout()
        self._connect_signals()

    def _create_widgets(self):
        """Create all input widgets."""
        # === Position Group ===
        self.label_lat = QLabel("Latitude:")
        self.spin_lat = QDoubleSpinBox()
        self.spin_lat.setRange(-90.0, 90.0)
        self.spin_lat.setValue(51.5)
        self.spin_lat.setDecimals(4)
        self.spin_lat.setSingleStep(0.01)

        self.label_lon = QLabel("Longitude:")
        self.spin_lon = QDoubleSpinBox()
        self.spin_lon.setRange(-180.0, 180.0)
        self.spin_lon.setValue(0.0)
        self.spin_lon.setDecimals(4)
        self.spin_lon.setSingleStep(0.01)

        # === Radar Parameters Group ===
        self.label_site_elev = QLabel("Site Elevation AMSL (m):")
        self.spin_site_elev = QSpinBox()
        self.spin_site_elev.setRange(0, 9000)
        self.spin_site_elev.setValue(100)
        self.spin_site_elev.setToolTip("Site elevation above mean sea level")

        self.label_mast_height = QLabel("Mast Height (m):")
        self.spin_mast_height = QSpinBox()
        self.spin_mast_height.setRange(0, 100)
        self.spin_mast_height.setValue(5)

        self.label_antenna_height = QLabel("Antenna Height (m):")
        self.spin_antenna_height = QSpinBox()
        self.spin_antenna_height.setRange(0, 50)
        self.spin_antenna_height.setValue(2)

        self.label_max_range = QLabel("Max Instrumented Range (km):")
        self.spin_max_range = QSpinBox()
        self.spin_max_range.setRange(0, 500)
        self.spin_max_range.setValue(100)

        # K-factor with slider + display
        self.label_k_factor = QLabel("K-Factor:")
        self.slider_k_factor = QSlider(Qt.Orientation.Horizontal)
        self.slider_k_factor.setRange(60, 150)  # 0.6 to 1.5 (× 100)
        self.slider_k_factor.setValue(133)  # 4/3 ≈ 1.333
        self.slider_k_factor.setSingleStep(1)
        self.slider_k_factor.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_k_factor.setTickInterval(10)

        self.label_k_value = QLabel(f"1.333")
        self.label_k_value.setMinimumWidth(40)

        # Diffraction guard angle with slider
        self.label_diffraction = QLabel("Diffraction Guard Angle (°):")
        self.slider_diffraction = QSlider(Qt.Orientation.Horizontal)
        self.slider_diffraction.setRange(0, 20)  # 0 to 2 degrees (× 0.1)
        self.slider_diffraction.setValue(5)  # 0.5°
        self.slider_diffraction.setSingleStep(1)
        self.slider_diffraction.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_diffraction.setTickInterval(2)

        self.label_diffraction_value = QLabel("0.5°")
        self.label_diffraction_value.setMinimumWidth(35)

        # === Height Bands (inline rows, no table) ===
        self._band_checkboxes: dict[int, QCheckBox] = {}
        self._height_bands_layout = QVBoxLayout()
        self._height_bands_layout.setSpacing(4)
        for height, info in HEIGHT_BANDS.items():
            row = QHBoxLayout()
            row.setSpacing(8)

            cb = QCheckBox(info["name"])
            cb.setChecked(True)
            self._band_checkboxes[height] = cb

            swatch = QLabel()
            swatch.setFixedSize(18, 18)
            swatch.setStyleSheet(
                f"background-color: {info['color']}; "
                f"border: 1px solid #555; border-radius: 2px;"
            )

            row.addWidget(cb)
            row.addWidget(swatch)
            row.addStretch()
            self._height_bands_layout.addLayout(row)

        # === DEM Source Group ===
        self.label_dem = QLabel("DEM File:")
        self.line_dem = QLineEdit()
        self.line_dem.setReadOnly(True)
        self.line_dem.setPlaceholderText("No DEM loaded")

        self.btn_load_dem = QPushButton("Browse DEM...")
        self.btn_load_dem.clicked.connect(self._on_load_dem_clicked)

        # === Obstructions CSV Group ===
        self.label_obstructions = QLabel("Obstructions CSV:")
        self.line_obstructions = QLineEdit()
        self.line_obstructions.setReadOnly(True)
        self.line_obstructions.setPlaceholderText("No obstructions loaded")

        self.btn_load_obstructions = QPushButton("Browse Obstructions...")
        self.btn_load_obstructions.clicked.connect(self._on_load_obstructions_clicked)

        # === Compute Button ===
        self.btn_compute = QPushButton("COMPUTE")
        self.btn_compute.setMinimumHeight(40)
        self.btn_compute.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.btn_compute.clicked.connect(self._on_compute_clicked)

        # === Export Buttons ===
        self.btn_export_geojson = QPushButton("Export GeoJSON")
        self.btn_export_geojson.clicked.connect(self._on_export_geojson_clicked)

        self.btn_export_png = QPushButton("Export PNG")
        self.btn_export_png.clicked.connect(self._on_export_png_clicked)

        # === Display Group ===
        self.label_coverage_opacity = QLabel("Coverage opacity:")
        self.slider_coverage_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_coverage_opacity.setRange(0, 100)
        self.slider_coverage_opacity.setValue(100)
        self.label_coverage_opacity_val = QLabel("100%")
        self.label_coverage_opacity_val.setMinimumWidth(38)

        self.label_shadow_opacity = QLabel("Shadow opacity:")
        self.slider_shadow_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_shadow_opacity.setRange(0, 100)
        self.slider_shadow_opacity.setValue(55)
        self.label_shadow_opacity_val = QLabel("55%")
        self.label_shadow_opacity_val.setMinimumWidth(38)

        # === Shadow mode combo ===
        self.combo_shadow_mode = QComboBox()
        self.combo_shadow_mode.addItem("Wedge",    userData="Wedge")
        self.combo_shadow_mode.addItem("Polygon (Smooth)", userData="Polygon")
        self.combo_shadow_mode.setToolTip(
            "Wedge: fast per-azimuth render.\n"
            "Polygon: shapely-merged smooth blobs (slower, better quality)."
        )

        # === Beam Angles Group ===
        self.label_min_beam = QLabel("Min beam angle (°):")
        self.spin_min_beam = QDoubleSpinBox()
        self.spin_min_beam.setRange(-90.0, 90.0)
        self.spin_min_beam.setValue(-90.0)
        self.spin_min_beam.setDecimals(1)
        self.spin_min_beam.setSingleStep(0.5)
        self.spin_min_beam.setToolTip("Minimum radar beam elevation angle (negative = below horizon)")

        self.label_max_beam = QLabel("Max beam angle (°):")
        self.spin_max_beam = QDoubleSpinBox()
        self.spin_max_beam.setRange(-90.0, 90.0)
        self.spin_max_beam.setValue(90.0)
        self.spin_max_beam.setDecimals(1)
        self.spin_max_beam.setSingleStep(0.5)
        self.spin_max_beam.setToolTip("Maximum radar beam elevation angle")

        self.label_beam_error = QLabel("")
        self.label_beam_error.setStyleSheet("color: #ff4444; font-size: 11px;")

        # === Top-K Site Finder Group ===
        # Bounding box spinboxes
        self.spin_bbox_min_lat = QDoubleSpinBox()
        self.spin_bbox_min_lat.setRange(-90.0, 90.0)
        self.spin_bbox_min_lat.setDecimals(4)
        self.spin_bbox_min_lat.setValue(51.0)
        self.spin_bbox_min_lat.setSuffix("° N")

        self.spin_bbox_max_lat = QDoubleSpinBox()
        self.spin_bbox_max_lat.setRange(-90.0, 90.0)
        self.spin_bbox_max_lat.setDecimals(4)
        self.spin_bbox_max_lat.setValue(51.5)
        self.spin_bbox_max_lat.setSuffix("° N")

        self.spin_bbox_min_lon = QDoubleSpinBox()
        self.spin_bbox_min_lon.setRange(-180.0, 180.0)
        self.spin_bbox_min_lon.setDecimals(4)
        self.spin_bbox_min_lon.setValue(0.0)
        self.spin_bbox_min_lon.setSuffix("° E")

        self.spin_bbox_max_lon = QDoubleSpinBox()
        self.spin_bbox_max_lon.setRange(-180.0, 180.0)
        self.spin_bbox_max_lon.setDecimals(4)
        self.spin_bbox_max_lon.setValue(0.5)
        self.spin_bbox_max_lon.setSuffix("° E")

        self.btn_draw_bbox = QPushButton("Draw Rectangle on Map")
        self.btn_draw_bbox.clicked.connect(self._on_draw_bbox_clicked)

        self.spin_top_k = QSpinBox()
        self.spin_top_k.setRange(1, 20)
        self.spin_top_k.setValue(5)
        self.spin_top_k.setPrefix("Top ")
        self.spin_top_k.setSuffix(" sites")

        self.spin_grid_step = QSpinBox()
        self.spin_grid_step.setRange(250, 2000)
        self.spin_grid_step.setSingleStep(250)
        self.spin_grid_step.setValue(1000)
        self.spin_grid_step.setSuffix(" m grid")

        self.label_site_estimate = QLabel("Set a bbox and grid step to estimate site count")
        self.label_site_estimate.setStyleSheet("color: #888; font-size: 11px;")
        self.label_site_estimate.setWordWrap(True)

        self.btn_find_top_k = QPushButton("Find Top-K Sites")
        self.btn_find_top_k.clicked.connect(self._on_find_top_k_clicked)

        self.btn_cancel_optimization = QPushButton("Cancel Search")
        self.btn_cancel_optimization.setVisible(False)
        self.btn_cancel_optimization.clicked.connect(lambda: self.cancel_optimization.emit())

        self.progress_bar_optimization = QProgressBar()
        self.progress_bar_optimization.setRange(0, 100)
        self.progress_bar_optimization.setValue(0)
        self.progress_bar_optimization.setVisible(False)

        self.results_widget = QWidget()
        self._results_layout = QVBoxLayout(self.results_widget)
        self._results_layout.setSpacing(4)
        self._results_layout.setContentsMargins(0, 0, 0, 0)


    def _create_layout(self):
        """Wrap all inputs in a QScrollArea so the panel is usable on short screens."""
        # Outer layout for this widget: just holds the scroll area
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        # Inner widget that holds all the actual content
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # === Position Group ===
        group_position = QGroupBox("Antenna Location (WGS84)")
        gp_layout = QVBoxLayout()
        gp_layout.addWidget(self.label_lat)
        gp_layout.addWidget(self.spin_lat)
        gp_layout.addWidget(self.label_lon)
        gp_layout.addWidget(self.spin_lon)
        group_position.setLayout(gp_layout)
        layout.addWidget(group_position)

        # === Radar Parameters Group ===
        group_radar = QGroupBox("Radar Parameters")
        gr_layout = QVBoxLayout()

        gr_layout.addWidget(self.label_site_elev)
        gr_layout.addWidget(self.spin_site_elev)

        gr_layout.addWidget(self.label_mast_height)
        gr_layout.addWidget(self.spin_mast_height)

        gr_layout.addWidget(self.label_antenna_height)
        gr_layout.addWidget(self.spin_antenna_height)

        gr_layout.addWidget(self.label_max_range)
        gr_layout.addWidget(self.spin_max_range)

        k_layout = QHBoxLayout()
        k_layout.addWidget(self.label_k_factor)
        k_layout.addWidget(self.slider_k_factor)
        k_layout.addWidget(self.label_k_value)
        gr_layout.addLayout(k_layout)

        diff_layout = QHBoxLayout()
        diff_layout.addWidget(self.label_diffraction)
        diff_layout.addWidget(self.slider_diffraction)
        diff_layout.addWidget(self.label_diffraction_value)
        gr_layout.addLayout(diff_layout)

        group_radar.setLayout(gr_layout)
        layout.addWidget(group_radar)

        # === Display Group ===
        group_display = QGroupBox("Display")
        gdisp_layout = QVBoxLayout()

        cov_row = QHBoxLayout()
        cov_row.addWidget(self.label_coverage_opacity)
        cov_row.addWidget(self.slider_coverage_opacity)
        cov_row.addWidget(self.label_coverage_opacity_val)
        gdisp_layout.addLayout(cov_row)

        shd_row = QHBoxLayout()
        shd_row.addWidget(self.label_shadow_opacity)
        shd_row.addWidget(self.slider_shadow_opacity)
        shd_row.addWidget(self.label_shadow_opacity_val)
        gdisp_layout.addLayout(shd_row)

        shadow_mode_row = QHBoxLayout()
        shadow_mode_row.addWidget(QLabel("Shadow style:"))
        self.combo_shadow_mode.currentIndexChanged.connect(self._on_shadow_mode_changed)
        shadow_mode_row.addWidget(self.combo_shadow_mode)
        gdisp_layout.addLayout(shadow_mode_row)

        group_display.setLayout(gdisp_layout)
        layout.addWidget(group_display)

        # === Height Bands Group ===
        group_heights = QGroupBox("Target Flight Heights")
        gh_layout = QVBoxLayout()
        gh_layout.addLayout(self._height_bands_layout)
        group_heights.setLayout(gh_layout)
        layout.addWidget(group_heights)

        # === Beam Angles Group ===
        group_beam = QGroupBox("Beam Elevation Angles")
        gb_layout = QVBoxLayout()
        beam_min_row = QHBoxLayout()
        beam_min_row.addWidget(self.label_min_beam)
        beam_min_row.addWidget(self.spin_min_beam)
        gb_layout.addLayout(beam_min_row)
        beam_max_row = QHBoxLayout()
        beam_max_row.addWidget(self.label_max_beam)
        beam_max_row.addWidget(self.spin_max_beam)
        gb_layout.addLayout(beam_max_row)
        gb_layout.addWidget(self.label_beam_error)
        group_beam.setLayout(gb_layout)
        layout.addWidget(group_beam)

        # === DEM Source Group ===
        group_dem = QGroupBox("DEM File")
        gd_layout = QVBoxLayout()
        dem_row = QHBoxLayout()
        dem_row.addWidget(self.line_dem)
        dem_row.addWidget(self.btn_load_dem)
        gd_layout.addLayout(dem_row)
        group_dem.setLayout(gd_layout)
        layout.addWidget(group_dem)

        # === Obstructions CSV Group ===
        group_obs = QGroupBox("Obstructions")
        go_layout = QVBoxLayout()
        obs_row = QHBoxLayout()
        obs_row.addWidget(self.line_obstructions)
        obs_row.addWidget(self.btn_load_obstructions)
        go_layout.addLayout(obs_row)
        group_obs.setLayout(go_layout)
        layout.addWidget(group_obs)

        # === Compute and Status ===
        layout.addWidget(self.btn_compute)

        # === Export Buttons ===
        layout.addWidget(self.btn_export_geojson)
        layout.addWidget(self.btn_export_png)

        # === Top-K Site Finder Group ===
        group_topk = QGroupBox("Top-K Site Finder")
        gt_layout = QVBoxLayout()

        # Bounding box
        gt_layout.addWidget(QLabel("Search Bounding Box:"))
        bbox_row1 = QHBoxLayout()
        bbox_row1.addWidget(QLabel("Min Lat:"))
        bbox_row1.addWidget(self.spin_bbox_min_lat)
        bbox_row1.addWidget(QLabel("Max Lat:"))
        bbox_row1.addWidget(self.spin_bbox_max_lat)
        gt_layout.addLayout(bbox_row1)

        bbox_row2 = QHBoxLayout()
        bbox_row2.addWidget(QLabel("Min Lon:"))
        bbox_row2.addWidget(self.spin_bbox_min_lon)
        bbox_row2.addWidget(QLabel("Max Lon:"))
        bbox_row2.addWidget(self.spin_bbox_max_lon)
        gt_layout.addLayout(bbox_row2)

        gt_layout.addWidget(self.btn_draw_bbox)

        # Settings row
        settings_row = QHBoxLayout()
        settings_row.addWidget(self.spin_top_k)
        settings_row.addWidget(self.spin_grid_step)
        gt_layout.addLayout(settings_row)

        gt_layout.addWidget(self.label_site_estimate)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_find_top_k)
        btn_row.addWidget(self.btn_cancel_optimization)
        gt_layout.addLayout(btn_row)

        gt_layout.addWidget(self.progress_bar_optimization)
        gt_layout.addWidget(self.results_widget)

        group_topk.setLayout(gt_layout)
        layout.addWidget(group_topk)

        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _connect_signals(self):
        """Connect internal signals (K-factor and diffraction sliders)."""
        self.slider_k_factor.valueChanged.connect(self._on_k_factor_changed)
        self.slider_diffraction.valueChanged.connect(self._on_diffraction_changed)
        self.slider_coverage_opacity.valueChanged.connect(self._on_coverage_opacity_changed)
        self.slider_shadow_opacity.valueChanged.connect(self._on_shadow_opacity_changed)

        # Bbox spinbox debounce: update map rectangle 300ms after user stops typing
        self._bbox_debounce_timer = QTimer(self)
        self._bbox_debounce_timer.setSingleShot(True)
        self._bbox_debounce_timer.timeout.connect(self._emit_bbox_changed)
        for sb in (self.spin_bbox_min_lat, self.spin_bbox_max_lat,
                   self.spin_bbox_min_lon, self.spin_bbox_max_lon):
            sb.valueChanged.connect(lambda _: self._bbox_debounce_timer.start(300))
        self.spin_grid_step.valueChanged.connect(lambda _: self._update_site_estimate())

    def _on_k_factor_changed(self, value):
        """Update K-factor display."""
        k = value / 100.0
        self.label_k_value.setText(f"{k:.3f}")

    def _on_diffraction_changed(self, value):
        """Update diffraction guard display."""
        angle = value * 0.1
        self.label_diffraction_value.setText(f"{angle:.1f}°")

    def _on_coverage_opacity_changed(self, value: int):
        """Emit coverage opacity as 0.0–1.0 fraction."""
        self.label_coverage_opacity_val.setText(f"{value}%")
        self.coverage_opacity_changed.emit(value / 100.0)

    def _on_shadow_opacity_changed(self, value: int):
        """Emit shadow opacity as 0.0–1.0 fraction."""
        self.label_shadow_opacity_val.setText(f"{value}%")
        self.shadow_opacity_changed.emit(value / 100.0)

    def _on_shadow_mode_changed(self, _index: int):
        mode = self.combo_shadow_mode.currentData()
        self.shadow_mode_changed.emit(mode)

    def _on_load_dem_clicked(self):
        """Open DEM file dialog."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load DEM File", "",
            "GeoTIFF (*.tif *.tiff);;All Files (*)"
        )
        if path:
            self.set_dem_path(path)

    def _on_load_obstructions_clicked(self):
        """Open obstructions CSV file dialog."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Obstructions CSV", "",
            "CSV (*.csv);;All Files (*)"
        )
        if path:
            self.set_obstructions_path(path)

    def _on_compute_clicked(self):
        """Handle compute button: gather parameters and emit signal."""
        if self.is_computing:
            return

        # Validate beam angles
        min_beam = self.spin_min_beam.value()
        max_beam = self.spin_max_beam.value()
        if min_beam >= max_beam:
            self.label_beam_error.setText("Min must be less than max.")
            return
        self.label_beam_error.setText("")

        # Gather parameters
        site_elev = self.spin_site_elev.value()
        request = ComputationRequest(
            radar_lat=self.spin_lat.value(),
            radar_lon=self.spin_lon.value(),
            site_elevation_amsl_m=float(site_elev),
            antenna_amsl_m=float(site_elev + self.spin_mast_height.value() + self.spin_antenna_height.value()),
            k_factor=self.slider_k_factor.value() / 100.0,
            max_range_km=self.spin_max_range.value(),
            height_bands_m=self._get_selected_height_bands(),
            diffraction_guard_deg=self.slider_diffraction.value() * 0.1,
            dem_path=self.dem_path,
            obstructions_path=self.obstructions_path,
            min_beam_deg=min_beam,
            max_beam_deg=max_beam,
        )

        # Update UI state
        self.is_computing = True
        self.btn_compute.setEnabled(False)

        # Emit signal
        self.compute_requested.emit(request)

    def _on_export_geojson_clicked(self):
        """Emit export GeoJSON signal."""
        self.export_geojson_requested.emit()

    def _on_export_png_clicked(self):
        """Emit export PNG signal."""
        # For now, just acknowledge
        pass

    def set_dem_path(self, path: str):
        """Set DEM path and update UI."""
        self.dem_path = path
        self.line_dem.setText(path)

    def set_obstructions_path(self, path: str):
        """Set obstructions path and update UI."""
        self.obstructions_path = path
        self.line_obstructions.setText(path)

    def _get_selected_height_bands(self) -> list[float]:
        """Return heights (m) whose checkbox is enabled."""
        return [float(h) for h, cb in self._band_checkboxes.items() if cb.isChecked()]

    def set_computing_finished(self):
        """Called by main window when computation finishes."""
        self.is_computing = False
        self.btn_compute.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Top-K Site Finder methods                                            #
    # ------------------------------------------------------------------ #

    def _on_draw_bbox_clicked(self):
        """Request map to enter rectangle draw mode."""
        self.draw_bbox_requested.emit()

    def _on_find_top_k_clicked(self):
        """Validate bbox and emit find_top_k_requested."""
        min_lat = self.spin_bbox_min_lat.value()
        max_lat = self.spin_bbox_max_lat.value()
        min_lon = self.spin_bbox_min_lon.value()
        max_lon = self.spin_bbox_max_lon.value()
        if min_lat >= max_lat or min_lon >= max_lon:
            return  # silently ignore degenerate bbox
        self.find_top_k_requested.emit(
            min_lat, min_lon, max_lat, max_lon,
            self.spin_top_k.value(),
            self.spin_grid_step.value(),
        )

    def _emit_bbox_changed(self):
        self.bbox_changed.emit(
            self.spin_bbox_min_lat.value(),
            self.spin_bbox_min_lon.value(),
            self.spin_bbox_max_lat.value(),
            self.spin_bbox_max_lon.value(),
        )
        self._update_site_estimate()

    def _update_site_estimate(self):
        import math
        step_m = self.spin_grid_step.value()
        min_lat = self.spin_bbox_min_lat.value()
        max_lat = self.spin_bbox_max_lat.value()
        min_lon = self.spin_bbox_min_lon.value()
        max_lon = self.spin_bbox_max_lon.value()
        if max_lat > min_lat and max_lon > min_lon and step_m > 0:
            lat_c = math.radians((min_lat + max_lat) / 2)
            n_rows = max(1, int((max_lat - min_lat) / (step_m / 111_000)) + 1)
            n_cols = max(1, int((max_lon - min_lon) / (step_m / (111_000 * math.cos(lat_c)))) + 1)
            n = n_rows * n_cols
            self.label_site_estimate.setText(f"~{n} candidate sites")
        else:
            self.label_site_estimate.setText("Set a bbox and grid step to estimate site count")

    def set_bbox(self, min_lat: float, min_lon: float, max_lat: float, max_lon: float):
        """Update bbox spinboxes from map draw event (called by MainWindow)."""
        self.spin_bbox_min_lat.setValue(min_lat)
        self.spin_bbox_max_lat.setValue(max_lat)
        self.spin_bbox_min_lon.setValue(min_lon)
        self.spin_bbox_max_lon.setValue(max_lon)

    def set_optimization_state(self, running: bool):
        """Toggle between Find / Cancel + progress bar."""
        self.btn_find_top_k.setVisible(not running)
        self.btn_cancel_optimization.setVisible(running)
        self.progress_bar_optimization.setVisible(running)
        if not running:
            self.progress_bar_optimization.setValue(0)

    def show_optimization_results(self, top_k: list):
        """
        Populate results_widget with ranked rows.
        top_k: [(lat, lon, score_km2), ...] sorted best-first
        """
        # Clear previous results
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for rank, (lat, lon, score) in enumerate(top_k, start=1):
            row = QHBoxLayout()
            row.setSpacing(6)
            rank_label = QLabel(f"#{rank}")
            rank_label.setMinimumWidth(24)
            coord_label = QLabel(f"{lat:.3f}°N {lon:.3f}°E")
            score_label = QLabel(f"{score:.0f} km²")
            score_label.setMinimumWidth(64)
            load_btn = QPushButton("Load")
            load_btn.setMaximumWidth(50)
            _lat, _lon = lat, lon   # capture for lambda
            load_btn.clicked.connect(lambda checked, la=_lat, lo=_lon: self.load_site_requested.emit(la, lo))
            row.addWidget(rank_label)
            row.addWidget(coord_label)
            row.addWidget(score_label)
            row.addWidget(load_btn)
            row_widget = QWidget()
            row_widget.setLayout(row)
            self._results_layout.addWidget(row_widget)

    def set_radar_position(self, lat: float, lon: float):
        """Update the radar lat/lon spinboxes (called when loading a top-K site)."""
        self.spin_lat.setValue(lat)
        self.spin_lon.setValue(lon)

    def build_request(self):
        """
        Build a ComputationRequest from current panel state.
        Used by OptimizationWorker as a template (it overrides lat/lon + resolution).
        """
        site_elev = self.spin_site_elev.value()
        return ComputationRequest(
            radar_lat=self.spin_lat.value(),
            radar_lon=self.spin_lon.value(),
            site_elevation_amsl_m=float(site_elev),
            antenna_amsl_m=float(site_elev + self.spin_mast_height.value() + self.spin_antenna_height.value()),
            k_factor=self.slider_k_factor.value() / 100.0,
            max_range_km=self.spin_max_range.value(),
            height_bands_m=self._get_selected_height_bands(),
            diffraction_guard_deg=self.slider_diffraction.value() * 0.1,
            dem_path=self.dem_path,
            obstructions_path=self.obstructions_path,
            min_beam_deg=self.spin_min_beam.value(),
            max_beam_deg=self.spin_max_beam.value(),
        )


