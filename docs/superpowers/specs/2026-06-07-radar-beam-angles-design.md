# Radar Beam Elevation Angles — Design Spec

**Date:** 2026-06-07  
**Feature:** Beam elevation angle controls (min/max)  
**Reference:** Cambridge Pixel SPx Radar Coverage Tool  
**Status:** Approved, ready for implementation

---

## Overview

Add min/max beam elevation angle controls that constrain radar coverage to the physical scan limits of the antenna. A target is in coverage only if it is both terrain-visible **and** falls within the beam elevation window. This matches the Cambridge Pixel SPx behaviour where "Beam angles (°)" and "Target heights (m)" coexist as independent constraints.

Default values of ±90° make the feature inert until the user changes them — zero behaviour change on existing sessions.

---

## Section 1: Data Model

### `ComputationRequest` (src/gui/control_panel.py)

Two new fields with safe defaults:

```python
min_beam_deg: float = -90.0   # no constraint by default
max_beam_deg: float = 90.0
```

### `coverage_data` dict format (emitted by worker via `coverage_computed` signal)

```python
# Before
coverage_data[h] = [(lat, lon), ...]

# After
coverage_data[h] = {
    "outer": [(lat, lon), ...],   # outer coverage boundary (terrain + beam limited)
    "inner": [(lat, lon), ...],   # donut hole inner boundary, or None if negligible
}
```

`inner` is `None` when `inner_r < RANGE_STEP_M` (degenerate — happens with ±90° defaults).

---

## Section 2: Computation (ComputationWorker.run)

Inside the per-height AGL loop, replace the single terrain visibility check with a two-gate combined mask:

```python
min_beam_rad = np.radians(req.min_beam_deg)
max_beam_rad = np.radians(req.max_beam_deg)

terrain_visible = target_angles[1:] >= horizon_angles[1:]
beam_within     = (target_angles[1:] >= min_beam_rad) & (target_angles[1:] <= max_beam_rad)
combined_mask   = terrain_visible & beam_within

visible_idx = np.where(combined_mask)[0]

# Outer boundary: last visible bin (unchanged semantics)
max_r = float(ranges[1:][visible_idx[-1]]) if visible_idx.size else float(ranges[1])
coverage_ranges_m[h_agl][i] = min(max_r, max_range_m)

# Inner boundary: first visible bin (new — donut hole from max_beam cutoff)
min_r = float(ranges[1:][visible_idx[0]]) if visible_idx.size else float(ranges[1])
inner_ranges_m[h_agl][i] = min_r
```

`inner_ranges_m` is a new parallel dict `{h: np.zeros(n_az)}` built alongside `coverage_ranges_m`.

At polygon-build time, if `inner_r < RANGE_STEP_M` for all azimuths → `inner = None` (no hole rendered).

**Shadow extraction is unchanged** — it uses `terrain_visible` alone. Beam blocking is not terrain blocking and must not appear as a shadow wedge.

---

## Section 3: UI Controls (ControlPanel)

New **"Beam Angles"** `QGroupBox` added to `ControlPanel`, placed below the height bands group:

| Control | Widget | Range | Default | Step |
|---|---|---|---|---|
| Min beam angle (°) | `QDoubleSpinBox` | −90 to +90 | −90.0 | 0.5 |
| Max beam angle (°) | `QDoubleSpinBox` | −90 to +90 | +90.0 | 0.5 |

Validation: if `min_beam_deg >= max_beam_deg` when compute is triggered, show an inline warning label and block the computation.

Both values are read into `ComputationRequest.min_beam_deg` / `max_beam_deg` in `_build_request()`.

---

## Section 4: Rendering (MapView)

`_build_coverage_features` updated to read the new dict format and produce multi-ring `latlngs`:

```python
outer = coverage_data[h]["outer"]
inner = coverage_data[h]["inner"]   # None or list of (lat, lon)

latlngs = [[[lat, lon] for lat, lon in outer]]
if inner is not None:
    latlngs.append([[lat, lon] for lat, lon in inner])
```

`L.polygon(latlngs)` in Leaflet natively renders a donut when two rings are passed — no JS changes required.

`_generate_leaflet_html` is unchanged since it already passes `latlngs` directly to `L.polygon()`.

---

## Section 5: Backwards Compatibility

| Scenario | Result |
|---|---|
| Default ±90° beam angles | `beam_within` always True → `combined_mask = terrain_visible` → identical output |
| `inner_r < RANGE_STEP_M` | `inner = None` → single outer ring → identical Leaflet rendering |
| Shadow extraction | Uses `terrain_visible` only — unaffected |
| Polar plot | Uses `horizon_angles` — unaffected |
| Existing Tier 1 tests | No API change to physics engines — all pass |
| Existing `MapView` area computation | Reads `coverage_data[h]["outer"]` — update the one call site in `_on_coverage_computed` |

---

## Implementation Order

1. **Git checkpoint** — commit current state before touching any code
2. `control_panel.py` — add beam angle spinboxes + validation, extend `ComputationRequest`
3. `main_window.py` (worker) — add `inner_ranges_m`, combined mask, new `coverage_data` format
4. `main_window.py` (`_on_coverage_computed`) — update area computation to read `["outer"]`
5. `map_view.py` — update `_build_coverage_features` for new dict format + multi-ring latlngs
6. Manual smoke test — default ±90° (should look identical), then set 0°/10° beam (should show donut)

---

## Out of Scope

- Per-height-band beam angles (global setting only, matching Cambridge Pixel)
- Beam angles affecting shadow layer
- Beam angles affecting polar OVD diagram
- Azimuth extent (separate Phase B item)
- Start range / inner donut hole via a separate "start range" spinbox (separate Phase B item)
