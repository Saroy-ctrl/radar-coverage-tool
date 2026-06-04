"""
GUI Module — PyQt6 desktop interface for radar coverage visualization.

Submodules:
- main_window: QMainWindow with dark theme, layout orchestration, QThread worker
- control_panel: Input widgets for radar parameters
- map_view: QWebEngineView + Folium map with GeoJSON overlay
- polar_view: Matplotlib polar diagram (OVD/RVD)
"""

from src.gui.main_window import MainWindow, ComputationWorker, ComputationRequest
from src.gui.control_panel import ControlPanel
from src.gui.map_view import MapView
from src.gui.polar_view import PolarView

__all__ = [
    "MainWindow",
    "ComputationWorker",
    "ComputationRequest",
    "ControlPanel",
    "MapView",
    "PolarView",
]
