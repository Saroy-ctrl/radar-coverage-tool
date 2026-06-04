# Map Cleanup Design — 2026-06-04

## Goal

Replace the visually noisy filled shadow wedges on the Leaflet map with thin radial lines,
add concentric range rings with labels, and add two live opacity sliders (one for coverage
polygons, one for shadow lines). The result should match the clean style of the Cambridge
Pixel SPx reference tool.

---

## Problem Statement

Current rendering has two issues:

1. **Shadow wedges are filled polygons** (`fillOpacity: 0.55`) that span from the coverage
   boundary to max range for every azimuth. At Ultra resolution (720 azimuths) this creates
   a "sunburst" pattern that overwhelms the coverage polygons.

2. **No range rings** — the map has no spatial reference grid, making it hard to read distances.

3. **No opacity controls** — coverage and shadow visibility are hardcoded; there is no way
   to tune them without recomputing.

---

## Scope

Primary changes in `src/gui/map_view.py`. The opacity sliders also touch
`src/gui/control_panel.py` (two new sliders + two new signals) and
`src/gui/main_window.py` (two new signal connections). No changes to the worker,
physics engines, or ComputationRequest.

---

## Design

### 1. Shadow rendering: filled wedges → thin radial lines

**`_build_shadow_features()`**

Change from building 4-corner wedge polygons to building 2-point centerlines:

- For each azimuth `i`, compute two points via `GEOD.fwd`:
  - Inner point: `(ant_lat, ant_lon)` projected `inner_r` metres at azimuth `az`
  - Outer point: `(ant_lat, ant_lon)` projected `max_range_m` metres at azimuth `az`
- Add **significance filter**: skip the azimuth if `inner_r >= 0.85 × max_range_m`
  (barely-blocked azimuths add noise without meaningful insight)
- Return `{"latlngs": [[lat_inner, lon_inner], [lat_outer, lon_outer]]}`

**`_generate_leaflet_html()` — shadow block**

Change from:
```javascript
L.polygon(latlngs, {
    color: '#8b0000', fillColor: '#8b0000',
    weight: 0, opacity: 0, fillOpacity: 0.55
}).addTo(map);
```

To:
```javascript
L.polyline(latlngs, {
    color: '#cc2200', weight: 1, opacity: 0.5,
    interactive: false
}).addTo(map);
```

---

### 2. Range rings

**New instance variable:** `self._max_range_m = 0.0` in `MapView.__init__`.
Updated in `set_shadow_data()` from `payload["max_range_m"]`.

**New method: `_generate_range_rings_js(lat, lon, max_range_m) -> str`**

Auto-select ring interval based on max range:

| max_range_m  | interval |
|-------------|----------|
| ≤ 25 000 m  | 5 000 m  |
| ≤ 50 000 m  | 10 000 m |
| ≤ 100 000 m | 20 000 m |
| > 100 000 m | 50 000 m |

For each ring at radius `r`:

1. **Circle:** `L.circle([lat, lon], {radius: r, color: 'rgba(255,255,255,0.22)', weight: 1, fill: false, interactive: false})`
2. **Label:** Place at the east point of the ring (computed server-side via `GEOD.fwd(lon, lat, 90, r)`).
   Rendered as `L.marker` with `L.divIcon` — white text, `font-size: 10px`, semi-transparent,
   no background/border. Text: `"Xkm"` (e.g. `"10 km"`).

Range rings are appended after coverage polygons in `_generate_leaflet_html()`.
They render whenever `self._max_range_m > 0` (i.e., after the first computation).

---

### 3. Opacity sliders

**ControlPanel — two new sliders in a new "Display" group** (placed after "Computation
Resolution", before "Target Flight Heights"):

| Slider | Range | Default | Signal emitted |
|--------|-------|---------|----------------|
| Coverage opacity | 0–100 | 100 | `coverage_opacity_changed(float)` — value 0.0–1.0 |
| Shadow opacity | 0–100 | 50 | `shadow_opacity_changed(float)` — value 0.0–1.0 |

Both sliders emit their signal immediately on every value change (no Compute needed).
Displayed as `QSlider(Horizontal)` with a `QLabel` showing the current `%` value.

**MapView — two new instance variables:**
- `self._coverage_opacity_factor = 1.0` — multiplier applied to each band's base opacity
- `self._shadow_opacity = 0.5` — absolute opacity for shadow polylines

**MapView — two new public methods:**
- `set_coverage_opacity(factor: float)` — updates `_coverage_opacity_factor`, calls `_render_map()`
- `set_shadow_opacity(opacity: float)` — updates `_shadow_opacity`, calls `_render_map()`

**Rendering:**

Coverage polygons: `actual_opacity = band_base_opacity × self._coverage_opacity_factor`
(preserves relative differences between height bands at every slider position).

Shadow polylines: use `self._shadow_opacity` directly for the `opacity` property.

**MainWindow — two new connections** in `_create_layout()`:
```python
self.control_panel.coverage_opacity_changed.connect(self.map_view.set_coverage_opacity)
self.control_panel.shadow_opacity_changed.connect(self.map_view.set_shadow_opacity)
```

---

## What Does NOT Change

- Signal wiring (`shadow_data_ready`, `computation_timed`, etc.)
- `_build_coverage_features()` — coverage polygons unchanged
- `set_shadow_data()` public signature — same dict format
- Physics engines, worker, ComputationRequest
- CLAUDE.md shadow color convention (`#8b0000`) — updated to `#cc2200` (brighter red,
  readable at `weight:1`)

---

## Testing

Manual verification:
1. Run app, load DEM, compute at Fast and Ultra resolution
2. Confirm shadow lines are thin radial strokes (not filled wedges)
3. Confirm barely-blocked azimuths (>85% of max range) are suppressed
4. Confirm range rings appear at correct intervals after first computation
5. Confirm labels are readable and positioned at east side of each ring
6. Confirm map still shows coverage polygons clearly on top
7. Drag coverage opacity slider to 0% — polygons disappear; to 50% — all bands at half opacity
8. Drag shadow opacity slider to 0% — shadow lines disappear; to 100% — fully opaque lines
9. Confirm sliders take effect instantly without recomputing

No new automated tests required — this is purely a rendering change with no
algorithmic logic.
