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
from PyQt6.QtCore import QUrl, QObject, pyqtSlot, pyqtSignal
try:
    from PyQt6.QtWebChannel import QWebChannel as _QWebChannel
    _HAS_WEBCHANNEL = True
except Exception:
    _QWebChannel = None
    _HAS_WEBCHANNEL = False

# Height band colors (Cambridge Pixel convention)
HEIGHT_BAND_COLORS = {
    50:   {"color": "#00cc44", "opacity": 0.45},
    100:  {"color": "#aacc00", "opacity": 0.38},
    500:  {"color": "#ff8800", "opacity": 0.32},
    1000: {"color": "#ff3300", "opacity": 0.22},
    3000: {"color": "#cc00ff", "opacity": 0.15},
}
DEFAULT_COLOR = {"color": "#4a9eff", "opacity": 0.40}

# Leaflet CDN (pinned version)
LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"


class MapBridge(QObject):
    """
    QObject exposed to JS via QWebChannel.
    JS calls bbox_selected/site_marker_clicked → Python signals fire.
    """
    bbox_received  = pyqtSignal(float, float, float, float)  # min_lat,min_lon,max_lat,max_lon
    marker_clicked = pyqtSignal(float, float, int)           # lat, lon, rank

    @pyqtSlot(float, float, float, float)
    def bbox_selected(self, min_lat: float, min_lon: float,
                      max_lat: float, max_lon: float):
        self.bbox_received.emit(min_lat, min_lon, max_lat, max_lon)

    @pyqtSlot(float, float, int)
    def site_marker_clicked(self, lat: float, lon: float, rank: int):
        self.marker_clicked.emit(lat, lon, rank)


class MapView(QWidget):
    """QWebEngineView-based map with direct Leaflet coverage overlay."""

    def __init__(self):
        super().__init__()
        self.web_engine = QWebEngineView()
        self.radar_lat = 51.5
        self.radar_lon = 0.0
        self.coverage_data = {}
        self._shadow_features = []      # pre-built wedge list, rebuilt on set_shadow_data
        self._coverage_opacity_factor = 1.0   # multiplier for all band opacities (0.0–1.0)
        self._shadow_opacity = 0.55           # absolute opacity for shadow polylines (0.0–1.0)
        self._max_range_m = 0.0               # set from shadow payload; drives range rings
        self._shadow_mode = "Wedge"           # "Wedge" | "Polygon"
        self._shadow_geojson = None           # stored GeoJSON dict for polygon mode
        self._shadow_segments_cache = []      # stored segments for polygon mode
        self._shadow_az_step_cache = 2.0

        # Fixed temp file path — overwritten cleanly each render
        self._tmp_path = Path(tempfile.gettempdir()) / "radar_map_view.html"

        # QWebChannel bridge: lets JS call Python methods (bbox draw, marker click).
        # QWebChannel is optional — if the DLL is unavailable the bridge is skipped
        # and the Top-K JS features degrade silently; the rest of the map still works.
        self._bridge = MapBridge(self)
        if _HAS_WEBCHANNEL:
            self._channel = _QWebChannel(self.web_engine.page())
            self._channel.registerObject("bridge", self._bridge)
            self.web_engine.page().setWebChannel(self._channel)
        else:
            self._channel = None

        # Top-K and bbox state — persisted here so _render_map() always includes them
        self._top_k_sites: list = []  # [(lat, lon, score_km2), ...] or []
        self._bbox: tuple | None = None  # (min_lat, min_lon, max_lat, max_lon) or None

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

    def _render_map(self, lat: float, lon: float,
                    coverage_features: list, shadow_features: list = None):
        """
        Write a fresh Leaflet HTML file and load it in the WebEngine.
        Shadow features are rendered first (underneath coverage polygons).

        Uses Path.write_text() (not a reused file handle) so the old HTML
        is always fully replaced — no stale-content / seek-without-truncate bugs.
        """
        html = self._generate_leaflet_html(lat, lon, coverage_features,
                                           shadow_features or [])
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

        Each feature: {height_m, area_km2, latlngs: [outerRing, ...optionalInnerRing]}
        latlngs is always a list-of-rings so L.polygon() renders a donut when two
        rings are present.

        Args:
            coverage_data: {height_m: {"outer": [(lat, lon), ...], "inner": [...] or None}}
                           Also accepts legacy format {height_m: [(lat, lon), ...]}
        """
        features = []
        for height_m, band_data in coverage_data.items():
            # Support both new dict format and legacy list format
            if isinstance(band_data, dict):
                outer_coords = list(band_data.get("outer", []))
                inner_coords = band_data.get("inner")
            else:
                outer_coords = list(band_data)
                inner_coords = None

            if not outer_coords:
                continue

            # Drop closing duplicate if present (Leaflet auto-closes polygons)
            if len(outer_coords) > 1 and outer_coords[0] == outer_coords[-1]:
                outer_coords = outer_coords[:-1]

            if len(outer_coords) < 3:
                continue

            area_km2 = self._compute_polygon_area_km2(outer_coords)

            # latlngs is a list of rings: first ring = outer, optional second = inner hole
            outer_ring = [[c[0], c[1]] for c in outer_coords]
            latlngs = [outer_ring]

            if inner_coords and len(inner_coords) >= 3:
                inner_list = list(inner_coords)
                if len(inner_list) > 1 and inner_list[0] == inner_list[-1]:
                    inner_list = inner_list[:-1]
                if len(inner_list) >= 3:
                    latlngs.append([[c[0], c[1]] for c in inner_list])

            features.append({
                "height_m": float(height_m),
                "area_km2": area_km2,
                "latlngs": latlngs,
            })

        # Sort: highest height first (drawn below lower bands so they don't obscure them)
        features.sort(key=lambda f: f["height_m"], reverse=True)
        return features

    def _build_shadow_features(
        self,
        ant_lat: float,
        ant_lon: float,
        ranges_m: list,
        azimuth_step_deg: float,
        max_range_m: float,
    ) -> list:
        """
        Build shadow wedge polygon features for terrain-blocked azimuths.

        Returns one 4-corner filled wedge polygon per blocked azimuth spanning
        from the coverage boundary out to max_range_m, with angular width
        equal to azimuth_step_deg. Azimuths where inner_r >= max_range_m are
        skipped (no shadow needed — coverage reaches the instrumented limit).

        Args:
            ant_lat, ant_lon: antenna WGS84 position
            ranges_m:         per-azimuth max visible range (lowest height band)
            azimuth_step_deg: azimuth resolution used in this run
            max_range_m:      instrumented range limit

        Returns:
            list of {"latlngs": [[lat, lon], ...]} dicts (4-corner wedge polygon)
        """
        from pyproj import Geod
        GEOD = Geod(ellps='WGS84')
        half = azimuth_step_deg / 2.0
        # Minimum inner radius so the wedge doesn't degenerate to a point at the antenna
        min_inner_r = 200.0
        features = []

        for i, inner_r in enumerate(ranges_m):
            if inner_r >= max_range_m:
                continue

            az = i * azimuth_step_deg
            az_l = az - half
            az_r = az + half
            r_in = max(float(inner_r), min_inner_r)

            lon_il, lat_il, _ = GEOD.fwd(ant_lon, ant_lat, az_l, r_in)
            lon_ir, lat_ir, _ = GEOD.fwd(ant_lon, ant_lat, az_r, r_in)
            lon_ol, lat_ol, _ = GEOD.fwd(ant_lon, ant_lat, az_l, max_range_m)
            lon_or, lat_or, _ = GEOD.fwd(ant_lon, ant_lat, az_r, max_range_m)

            features.append({
                "latlngs": [
                    [float(lat_il), float(lon_il)],
                    [float(lat_ol), float(lon_ol)],
                    [float(lat_or), float(lon_or)],
                    [float(lat_ir), float(lon_ir)],
                ]
            })

        return features

    def _generate_range_rings_js(self, lat: float, lon: float, max_range_m: float) -> str:
        """
        Generate Leaflet JS for concentric range rings with distance labels.

        Auto-selects ring interval based on max_range_m:
            <= 25 km →  5 km
            <= 50 km → 10 km
            <=100 km → 20 km
            > 100 km → 50 km

        Labels placed at east point of each ring (computed via pyproj Geod.fwd).
        """
        if max_range_m <= 0:
            return ""

        from pyproj import Geod
        geod = Geod(ellps='WGS84')

        if max_range_m <= 25_000:
            interval_m = 5_000
        elif max_range_m <= 50_000:
            interval_m = 10_000
        elif max_range_m <= 100_000:
            interval_m = 20_000
        else:
            interval_m = 50_000

        blocks = []
        r = interval_m
        while r <= max_range_m:
            lon_e, lat_e, _ = geod.fwd(lon, lat, 90, r)
            label = f"{r / 1000:.0f} km" if r >= 1000 else f"{r:.0f} m"
            blocks.append(f"""L.circle([{lat:.6f}, {lon:.6f}], {{
    radius: {r:.0f},
    color: 'rgba(255,255,255,0.22)',
    weight: 1,
    fill: false,
    interactive: false
}}).addTo(map);
L.marker([{lat_e:.6f}, {lon_e:.6f}], {{
    icon: L.divIcon({{
        className: '',
        html: '<span style="color:rgba(255,255,255,0.6);font-size:10px;font-family:sans-serif;white-space:nowrap;text-shadow:0 0 3px #000">{label}</span>',
        iconAnchor: [0, 8]
    }}),
    interactive: false
}}).addTo(map);""")
            r += interval_m

        return "\n".join(blocks)

    def _generate_bbox_js(self) -> str:
        """Generate JS to draw the bbox rectangle if one is set."""
        bbox = getattr(self, '_bbox', None)
        if bbox is None:
            return ""
        min_lat, min_lon, max_lat, max_lon = bbox
        return (
            f"L.rectangle([[{min_lat},{min_lon}],[{max_lat},{max_lon}]], "
            f"{{color:'#4488ff',weight:2,fill:false,dashArray:'6 4',interactive:false}}"
            f").addTo(map);"
        )

    def _generate_top_k_js(self) -> str:
        """Generate JS to draw top-K ranked circle markers."""
        if not getattr(self, '_top_k_sites', None):
            return ""
        colors = ["'#ffd700'", "'#c0c0c0'", "'#cd7f32'"]
        blocks = []
        for rank, (lat, lon, score) in enumerate(self._top_k_sites, start=1):
            color = colors[rank - 1] if rank <= 3 else "'#4fc3f7'"
            tooltip = f"#{rank} &mdash; {score:.0f} km&sup2;"
            blocks.append(
                f"(function(lat,lon,rank){{"
                f"L.circleMarker([lat,lon],{{radius:13,color:'#fff',weight:2,"
                f"fillColor:{color},fillOpacity:0.92}})"
                f".bindTooltip('{tooltip}',{{permanent:false}})"
                f".on('click',function(){{if(bridge)bridge.site_marker_clicked(lat,lon,rank);}})"
                f".addTo(map);"
                f"}})({lat},{lon},{rank});"
            )
        return "\n        ".join(blocks)

    def _generate_leaflet_html(self, lat: float, lon: float,
                               coverage_features: list,
                               shadow_features: list = None) -> str:
        """
        Generate a standalone HTML page with Leaflet + coverage polygons.

        Coverage polygons are added as plain L.polygon() calls so there is
        no dependency on Folium's layer generation (which can fail silently
        inside QWebEngineView's local-file security sandbox).

        Args:
            lat, lon: map centre / antenna location
            coverage_features: list of feature dicts from _build_coverage_features()
            shadow_features: list of {"latlngs": [[lat, lon], ...]} dicts
        """
        # Shadow wedge polygons (rendered first — underneath coverage)
        shadow_blocks = []

        if self._shadow_mode == "Polygon" and self._shadow_geojson:
            # Inject pre-built merged GeoJSON as L.geoJSON() layer
            geojson_str = json.dumps(self._shadow_geojson, separators=(',', ':'))
            shadow_blocks.append(f"""\
L.geoJSON({geojson_str}, {{
    style: {{
        color: '#8b0000',
        fillColor: '#8b0000',
        weight: 0,
        fillOpacity: {round(self._shadow_opacity, 4)}
    }},
    interactive: false
}}).addTo(map);""")
        else:
            # Wedge mode: individual 4-corner polygon per blocked azimuth
            for sfeat in (shadow_features or []):
                slatlngs_json = json.dumps(sfeat["latlngs"], separators=(',', ':'))
                shadow_blocks.append(f"""\
L.polygon({slatlngs_json}, {{
    color: '#8b0000',
    fillColor: '#8b0000',
    weight: 0,
    fillOpacity: {round(self._shadow_opacity, 4)},
    interactive: false
}}).addTo(map);""")

        # Build JavaScript for each polygon
        polygon_blocks = []
        for feat in coverage_features:
            h      = feat["height_m"]
            area   = feat["area_km2"]
            latlngs = feat["latlngs"]
            ci      = self._get_color_info(h)
            color   = ci["color"]
            opacity = round(ci["opacity"] * self._coverage_opacity_factor, 4)

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

        shadows_js  = "\n        ".join(shadow_blocks)
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

        range_rings_js = self._generate_range_rings_js(lat, lon, self._max_range_m)
        bbox_js = self._generate_bbox_js()
        topk_js = self._generate_top_k_js()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Radar Coverage Map</title>
  <link rel="stylesheet" href="{LEAFLET_CSS}"/>
  <script src="{LEAFLET_JS}"></script>
  <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
  <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
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

    // ── shadow (terrain-blocked) zones ─────────────────────────────────
    {shadows_js}

    // ── coverage polygons ───────────────────────────────────────────────
    {polygons_js}

    // ── range rings ──────────────────────────────────────────────────────
    {range_rings_js}

    // ── bbox rectangle ───────────────────────────────────────────────────
    {bbox_js}

    // ── top-K site markers ───────────────────────────────────────────────
    {topk_js}

    // ── QWebChannel bridge ────────────────────────────────────────────────
    var bridge = null;
    if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined') {{
        new QWebChannel(qt.webChannelTransport, function(channel) {{
            bridge = channel.objects.bridge;
        }});
    }}

    // ── Leaflet.draw — rectangle only ─────────────────────────────────────
    var drawnItems = new L.FeatureGroup().addTo(map);
    var drawControl = new L.Control.Draw({{
        draw: {{
            rectangle: true,
            polygon: false, polyline: false,
            circle: false, marker: false, circlemarker: false
        }},
        edit: {{ featureGroup: drawnItems }}
    }});
    map.addControl(drawControl);

    map.on(L.Draw.Event.CREATED, function(e) {{
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);
        var b = e.layer.getBounds();
        if (bridge) {{
            bridge.bbox_selected(b.getSouth(), b.getWest(), b.getNorth(), b.getEast());
        }}
    }});

    // ── enableDrawMode: activate Leaflet.draw rectangle tool programmatically
    function enableDrawMode() {{
        new L.Draw.Rectangle(map, drawControl.options.draw.rectangle).enable();
    }}
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
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def set_antenna_location(self, lat: float, lon: float):
        """
        Update antenna location marker, preserving existing coverage and shadow.

        Preserves existing coverage so the map isn't wiped when the window
        pans to the antenna before computation finishes.
        """
        self.radar_lat = lat
        self.radar_lon = lon
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(lat, lon, features, self._shadow_features)

    def set_shadow_data(self, ant_lat: float, ant_lon: float, payload: dict):
        """
        Rebuild shadow geometry from computation payload and re-render.

        payload keys:
            "ranges_m"        — per-azimuth last-visible range (wedge mode)
            "shadow_segments" — list of (az_deg, inner_r, outer_r) tuples (polygon mode)
            "azimuth_step_deg"
            "max_range_m"
        """
        self._max_range_m = payload["max_range_m"]
        self._shadow_az_step_cache = payload["azimuth_step_deg"]
        self._shadow_segments_cache = payload.get("shadow_segments", [])

        # Always build the wedge features (used in Wedge mode and as fallback)
        self._shadow_features = self._build_shadow_features(
            ant_lat=ant_lat,
            ant_lon=ant_lon,
            ranges_m=payload["ranges_m"],
            azimuth_step_deg=payload["azimuth_step_deg"],
            max_range_m=payload["max_range_m"],
        )

        # Build merged polygon GeoJSON if in Polygon mode
        if self._shadow_mode == "Polygon":
            from src.shadow_builder import build_merged_shadow_geojson
            self._shadow_geojson = build_merged_shadow_geojson(
                ant_lat, ant_lon,
                self._shadow_segments_cache,
                payload["azimuth_step_deg"],
            )
        else:
            self._shadow_geojson = None

        features = self._build_coverage_features(self.coverage_data)
        self._render_map(ant_lat, ant_lon, features, self._shadow_features)

    def set_dem_bounds(self, bounds: dict):
        """
        Optionally zoom the map to DEM extent after loading.

        Args:
            bounds: {{"north": float, "south": float, "east": float, "west": float}}
        """
        pass  # Could call fitBounds via JS if needed

    def set_coverage_opacity(self, factor: float):
        """Update coverage opacity multiplier and re-render. factor in [0.0, 1.0]."""
        self._coverage_opacity_factor = max(0.0, min(1.0, factor))
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def set_shadow_opacity(self, opacity: float):
        """Update shadow line opacity and re-render. opacity in [0.0, 1.0]."""
        self._shadow_opacity = max(0.0, min(1.0, opacity))
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def set_shadow_mode(self, mode: str):
        """
        Switch shadow rendering mode. Triggers a re-render with current data.
        mode: "Wedge" (per-azimuth wedge polygons) or "Polygon" (shapely-merged blobs)
        """
        self._shadow_mode = mode
        if mode == "Polygon" and self._shadow_segments_cache:
            from src.shadow_builder import build_merged_shadow_geojson
            self._shadow_geojson = build_merged_shadow_geojson(
                self.radar_lat, self.radar_lon,
                self._shadow_segments_cache,
                self._shadow_az_step_cache,
            )
        else:
            self._shadow_geojson = None
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def enable_draw_mode(self):
        """Tell Leaflet to activate the rectangle draw tool."""
        self.web_engine.page().runJavaScript("enableDrawMode()")

    def show_bbox_rect(self, min_lat: float, min_lon: float,
                       max_lat: float, max_lon: float):
        """Store bbox state and re-render map with blue dashed rectangle."""
        self._bbox = (min_lat, min_lon, max_lat, max_lon)
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def show_top_k_sites(self, top_k: list):
        """
        Store top-K sites and re-render map with numbered circle markers.
        top_k: [(lat, lon, score_km2), ...] sorted best-first
        """
        self._top_k_sites = list(top_k)
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def clear_top_k(self):
        """Remove top-K markers from map and re-render."""
        self._top_k_sites = []
        features = self._build_coverage_features(self.coverage_data)
        self._render_map(self.radar_lat, self.radar_lon, features, self._shadow_features)

    def cleanup(self):
        """Remove the temporary HTML file."""
        try:
            self._tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
