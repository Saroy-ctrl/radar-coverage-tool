"""
Polar View — Matplotlib embedded polar diagram (OVD/RVD).

Responsibilities:
- Matplotlib figure embedded in PyQt6
- Polar plot: theta = azimuth (compass: theta_zero='N', theta_direction=-1)
- r = max_range for selected height band
- Draw horizon obstruction as filled area
- Overlay coverage range as colored circle/arc per height
- Legend: height band colors
- Toolbar: pan/zoom/save
- No blocking of main thread
"""

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel


class _RadarToolbar(NavigationToolbar2QT):
    """Minimal toolbar: only Home (reset view) and Save (multi-format export)."""
    toolitems = [t for t in NavigationToolbar2QT.toolitems
                 if t[0] in ('Home', 'Save')]

# Height band colors (Cambridge Pixel convention)
HEIGHT_BAND_COLORS = {
    50: "#00cc44",
    100: "#aacc00",
    500: "#ff8800",
    1000: "#ff3300",
    3000: "#cc00ff",
}

# Dark background
DARK_BG = "#1e1e2e"
DARK_PANEL = "#2a2a3e"
ACCENT_TEXT = "#e8e8e8"


class PolarView(QWidget):
    """Matplotlib polar diagram embedded in PyQt6."""

    def __init__(self):
        super().__init__()
        self.fig = None
        self.ax = None
        self.canvas = None
        self.toolbar = None
        self.current_height = 500  # Default selected height
        self.coverage_data = {}
        self.horizon_data = None
        self._band_colors: dict = dict(HEIGHT_BAND_COLORS)  # mutable copy

        self._create_widgets()
        self._create_initial_plot()

    def _create_widgets(self):
        """Create matplotlib figure and toolbar."""
        # Create figure with dark background
        self.fig = Figure(figsize=(8, 8), dpi=100)
        self.fig.patch.set_facecolor(DARK_BG)

        # Create canvas
        self.canvas = FigureCanvas(self.fig)

        # Create toolbar
        self.toolbar = _RadarToolbar(self.canvas, self)
        self._style_toolbar()

        # Create layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)

        self.setMinimumHeight(200)

    def _style_toolbar(self):
        """Apply dark theme to toolbar."""
        self.toolbar.setStyleSheet(f"""
            QToolBar {{
                background-color: {DARK_PANEL};
                border-bottom: 1px solid #444;
            }}
            QToolButton {{
                background-color: {DARK_PANEL};
                color: {ACCENT_TEXT};
                border: 1px solid #555;
                padding: 2px;
                margin: 2px;
                border-radius: 2px;
            }}
            QToolButton:hover {{
                background-color: #4a9eff;
            }}
            QToolButton:pressed {{
                background-color: #2b8fe0;
            }}
        """)

    def _create_initial_plot(self):
        """Create initial empty polar plot."""
        self.fig.clear()
        self.ax = self.fig.add_subplot(111, projection='polar')

        # Configure polar plot for compass convention
        # theta_zero='N' means 0° is North
        # theta_direction=-1 means clockwise (compass convention)
        self.ax.set_theta_zero_location('N')
        self.ax.set_theta_direction(-1)

        # Set grid and labels
        self.ax.set_facecolor(DARK_BG)
        self.ax.grid(True, color='#555', linestyle='--', alpha=0.5)

        # Style axes
        self.ax.tick_params(colors=ACCENT_TEXT, labelsize=10)
        for spine in self.ax.spines.values():
            spine.set_edgecolor('#555')

        # Labels
        self.ax.set_xlabel("Azimuth (°)", color=ACCENT_TEXT, labelpad=20)
        self.ax.set_ylabel("Range (km)", color=ACCENT_TEXT, labelpad=40)
        self.ax.set_title("Coverage Diagram (OVD)", color=ACCENT_TEXT, pad=8, fontsize=11, fontweight='bold')
        self.fig.tight_layout(pad=0.5)
        self.canvas.draw()

    def set_height_band_config(self, bands: list):
        """Update band colour table from control panel config."""
        self._band_colors = {b["height_m"]: b["color"] for b in bands}

    def update_data(self, polar_data: dict):
        """
        Update polar diagram with new data.

        Args:
            polar_data: {
                "azimuths": list[360] or list[n_azimuths],  # degrees [0, 360)
                "ranges": {height_m: list[n_azimuths]},     # in km
                "horizon": list[n_azimuths],                # in degrees (elevation angle)
            }
        """
        self.coverage_data = polar_data.get("ranges", {})
        self.horizon_data = polar_data.get("horizon", None)
        azimuths_deg = polar_data.get("azimuths", list(range(0, 360)))

        # Convert azimuths to radians
        azimuths_rad = np.deg2rad(azimuths_deg)

        # Redraw plot
        self.fig.clear()
        self.ax = self.fig.add_subplot(111, projection='polar')

        # Configure compass convention
        self.ax.set_theta_zero_location('N')
        self.ax.set_theta_direction(-1)

        # Set background
        self.ax.set_facecolor(DARK_BG)

        # Draw horizon obstruction as filled area
        if self.horizon_data is not None:
            horizon_rad = np.deg2rad(self.horizon_data)
            self.ax.fill_between(
                azimuths_rad, 0, horizon_rad,
                color='#8b0000', alpha=0.3, label='Horizon (obstruction)'
            )
            self.ax.plot(azimuths_rad, horizon_rad, color='#cc0000', linewidth=1.5, label='Horizon')

        # Draw coverage arcs for each height band (sorted highest first for visual clarity)
        heights_sorted = sorted(self.coverage_data.keys(), reverse=True)
        for height_m in heights_sorted:
            ranges_km = self.coverage_data[height_m]
            color = self._band_colors.get(height_m) or self._band_colors.get(int(height_m), "#4a9eff")

            # Plot as filled area
            self.ax.fill_between(
                azimuths_rad, 0, ranges_km,
                color=color, alpha=0.3
            )

            # Plot boundary line
            self.ax.plot(
                azimuths_rad, ranges_km,
                color=color, linewidth=2, label=f'{height_m}m AGL'
            )

        # Grid and labels
        self.ax.grid(True, color='#555', linestyle='--', alpha=0.5)
        self.ax.tick_params(colors=ACCENT_TEXT, labelsize=10)

        # Spine color
        for spine in self.ax.spines.values():
            spine.set_edgecolor('#555')

        # Labels
        self.ax.set_xlabel("Azimuth (°)", color=ACCENT_TEXT, labelpad=20, fontsize=10)
        self.ax.set_ylabel("Range (km)", color=ACCENT_TEXT, labelpad=40, fontsize=10)
        self.ax.set_title("Coverage Diagram (OVD)", color=ACCENT_TEXT, pad=8, fontsize=11, fontweight='bold')

        # Legend with dark background
        legend = self.ax.legend(
            loc='upper right', bbox_to_anchor=(1.25, 1.1),
            framealpha=0.9, fancybox=True, shadow=False,
            fontsize=9
        )
        if legend:
            legend.get_frame().set_facecolor(DARK_PANEL)
            legend.get_frame().set_edgecolor('#555')
            for text in legend.get_texts():
                text.set_color(ACCENT_TEXT)

        # Redraw canvas
        self.fig.tight_layout(pad=0.5)
        self.canvas.draw()

    def select_height_band(self, height_m: float):
        """Select which height band to highlight (for future interactive features)."""
        self.current_height = height_m

    def save_as_png(self, filename: str):
        """
        Save current figure as PNG.

        Args:
            filename: output file path
        """
        try:
            self.fig.savefig(
                filename, dpi=150, facecolor=DARK_BG, edgecolor='none',
                bbox_inches='tight', pad_inches=0.2
            )
            return True
        except Exception as e:
            print(f"Error saving PNG: {e}")
            return False

    def clear(self):
        """Clear the plot."""
        self.coverage_data = {}
        self.horizon_data = None
        self._create_initial_plot()
