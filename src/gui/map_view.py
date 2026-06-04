"""
Map View — QWebEngineView with a hand-written Leaflet HTML map and coverage overlay.

Replaced Folium with a direct HTML/JS generator so coverage polygons are added
as plain L.polygon() calls — no folium black-box layer-building that fails silently.

Responsibilities:
- Base layer: OpenStreetMap tiles via Leaflet CDN
- Draw antenna location as a circle marker
- Overlay coverage polygons per height band using Cambridge Pixel colors
- Draw highest height first (largest area, painted below lower bands)
- Tooltip on hover: height band + area km²
"""

import json
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtCore import QUrl

# Height band colors (Cambridge Pixel convention)
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},
    100:  {"color": "#aacc00", "opacity": 0.40},
    500:  {"color": "#ff8800", "opacity": 0.40},
    1000: {"color": "#ff3300", "opacity": 0.35},
    3000: {"color": "#cc00ff", "opacity": 0.30},
}
DEFAULT_COLOR = {"color": "#4a9eff", "opacity": 0.40}

# Leaflet CDN (pinned version)
LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"


class MapView(QWidget):
    """QWebEngineView-based map with direct Leaflet coverage overlay."""

    def __init__(self):
        super().__init__()
        self.web_engine = QWebEngineView()
        self.radar_lat = 51.5
        self.radar_lon = 0.0
        self.coverage_data = {}

        # Fixed temp file path — overwritten cleanly each render
        self._tmp_path = Path(tempfile.gettempdir()) / "radar_map_view.html"

        # Allow the local file:// page to load Leaflet + tile CDN URLs.
        # Without this, QWebEngine's security sandbox blocks remote URLs,
        # causing "L is not defined" JS errors even though Leaflet is on CDN.
        settings = self.web_engine.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )

        self._create_layout()
        self._create_initial_map()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _create_layout(self):
        """Create layout with web engine view."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web_engine)

    def _create_initial_map(self):
        """Render the initial empty map."""
        self._render_map(self.radar_lat, self.radar_lon, [])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _render_map(self, lat: float, lon: float, coverage_features: list):
        """
        Write a fresh Leaflet HTML file and load it in the WebEngine.

        Uses Path.write_text() (not a reused file handle) so the old HTML
        is always fully replaced — no stale-content / seek-without-truncate bugs.
        """
        html = self._generate_leaflet_html(lat, lon, coverage_features)
        self._tmp_path.write_text(html, encoding="utf-8")

        # Navigate to about:blank first to force a full reload of the local file
        # (QWebEngineView caches same-URL navigations).
        self.web_engine.setUrl(QUrl("about:blank"))
        self.web_engine.setUrl(QUrl.fromLocalFile(str(self._tmp_path)))

    def _get_color_info(self, height_m) -> dict:
        """Return color dict for height band, falling back to DEFAULT_COLOR."""
        # Try exact int match first, then int-cast (handles float keys like 50.0)
        ci = HEIGHT_BAND_COLORS.get(height_m)
        if ci is None:
            ci = HEIGHT_BAND_COLORS.get(int(height_m), DEFAULT_COLOR)
        return ci

    def _compute_polygon_area_km2(self, coords_latlon: list) -> float:
        """
        Compute geodetic polygon area in km² using pyproj + shapely.

        Args:
            coords_latlon: list of (lat, lon) tuples (NOT closed)
        """
        try:
            from pyproj import Geod
            from shapely.geometry import Polygon
            geod = Geod(ellps="WGS84")
            poly = Polygon([(c[1], c[0]) for c in coords_latlon])
            area_m2, _ = geod.geometry_area_perimeter(poly)
            return abs(area_m2) / 1e6
        except Exception:
            return 0.0

    def _build_coverage_features(self, coverage_data: dict) -> list:
        """
        Convert coverage_data dict into a list of feature dicts used by
        _generate_leaflet_html.

        Each feature: {height_m, area_km2, latlngs: [[lat, lon], ...]}

        Args:
            coverage_data: {height_m: [(lat, lon), ...]}  — ring may or may not be closed
        """
        features = []
        for height_m, coords in coverage_data.items():
            coords_list = list(coords)
            if not coords_list:
                continue

            # Drop the closing duplicate if present (Leaflet auto-closes polygons)
            if len(coords_list) > 1 and coords_list[0] == coords_list[-1]:
                coords_list = coords_list[:-1]

            if len(coords_list) < 3:
                continue

            area_km2 = self._compute_polygon_area_km2(coords_list)

            features.append({
                "height_m": float(height_m),
                "area_km2": area_km2,
                # Leaflet L.polygon takes [[lat, lon], ...] (no closing point needed)
                "latlngs": [[c[0], c[1]] for c in coords_list],
            })

        # Sort: highest height first (drawn below lower bands so they don't obscure them)
        features.sort(key=lambda f: f["height_m"], reverse=True)
        return features

    def _generate_leaflet_html(self, lat: float, lon: float,
                               coverage_features: list) -> str:
        """
        Generate a standalone HTML page with Leaflet + coverage polygons.

        Coverage polygons are added as plain L.polygon() calls so there is
        no dependency on Folium's layer generation (which can fail silently
        inside QWebEngineView's local-file security sandbox).

        Args:
            lat, lon: map centre / antenna location
            coverage_features: list of feature dicts from _build_coverage_features()
        """
        # Build JavaScript for each polygon
        polygon_blocks = []
        for feat in coverage_features:
            h      = feat["height_m"]
            area   = feat["area_km2"]
            latlngs = feat["latlngs"]
            ci     = self._get_color_info(h)
            color  = ci["color"]
            opacity = ci["opacity"]

            # Serialise coordinate array to compact JSON
            latlngs_json = json.dumps(latlngs, separators=(',', ':'))

            tooltip = f"{h:.0f}m AGL &mdash; {area:.0f} km&sup2;"

            polygon_blocks.append(f"""\
L.polygon({latlngs_json}, {{
    color: '{color}',
    fillColor: '{color}',
    weight: 1,
    opacity: {opacity},
    fillOpacity: {opacity}
}}).bindTooltip('{tooltip}', {{sticky: true}}).addTo(map);""")

        polygons_js = "\n        ".join(polygon_blocks)

        # Legend entries
        legend_rows = []
        for h in sorted([f["height_m"] for f in coverage_features], reverse=True):
            ci = self._get_color_info(h)
            legend_rows.append(
                f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
                f'<span style="width:14px;height:14px;background:{ci["color"]};'
                f'display:inline-block;border-radius:2px"></span>'
                f'<span>{h:.0f}m AGL</span></div>'
            )
        legend_html = "\n".join(legend_rows) if legend_rows else ""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Radar Coverage Map</title>
  <link rel="stylesheet" href="{LEAFLET_CSS}"/>
  <script src="{LEAFLET_JS}"></script>
  <style>
    html, body, #map {{
      margin: 0; padding: 0;
      height: 100%; width: 100%;
      background: #1e1e2e;
    }}
    #legend {{
      position: absolute;
      bottom: 24px; right: 8px;
      background: rgba(30,30,46,0.88);
      color: #e8e8e8;
      border: 1px solid #555;
      border-radius: 6px;
      padding: 8px 12px;
      font: 12px/1.4 sans-serif;
      z-index: 1000;
      min-width: 120px;
    }}
    #legend h4 {{
      margin: 0 0 6px; font-size: 12px; color: #aaa;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="legend">
    <h4>Coverage Bands</h4>
    {legend_html}
  </div>
  <script>
    // ── map init ────────────────────────────────────────────────────────
    var map = L.map('map', {{
      center: [{lat:.6f}, {lon:.6f}],
      zoom: 8,
      preferCanvas: true
    }});

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19
    }}).addTo(map);

    // ── antenna marker ──────────────────────────────────────────────────
    L.circleMarker([{lat:.6f}, {lon:.6f}], {{
      radius: 8,
      color: '#ffffff',
      fillColor: '#4a9eff',
      fillOpacity: 1,
      weight: 2
    }}).bindPopup(
      '<b>Radar Antenna</b><br>Lat: {lat:.4f}&deg;<br>Lon: {lon:.4f}&deg;'
    ).addTo(map);

    // ── coverage polygons ───────────────────────────────────────────────
    {polygons_js}
  </script>
</body>
</html>"""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_coverage(self, coverage_data: dict):
        """
        Update map with new coverage polygons.

        Args:
            coverage_data: {{height_m: [(lat, lon), ...]}}
        """
        self.coverage_data = coverage_data
        features = self._build_coverage_features(coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features)

    def set_antenna_location(self, lat: float, lon: float):
        """
        Update antenna location marker.

        Preserves existing coverage so the map isn't wiped when the window
        pans to the antenna before computation finishes.
        """
        self.radar_lat = lat
        self.radar_lon = lon
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(lat, lon, features)

    def set_dem_bounds(self, bounds: dict):
        """
        Optionally zoom the map to DEM extent after loading.

        Args:
            bounds: {{"north": float, "south": float, "east": float, "west": float}}
        """
        pass  # Could call fitBounds via JS if needed

    def cleanup(self):
        """Remove the temporary HTML file."""
        try:
            self._tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
