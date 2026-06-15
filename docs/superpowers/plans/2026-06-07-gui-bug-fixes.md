# GUI Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six confirmed GUI bugs: stuck compute button, non-scrollable control panel, cramped height-bands table, polar OVD title clipped when map is maximised, invisible QSpinBox arrows in dark theme, and mismatched shadow-opacity initial value.

**Architecture:** All changes are confined to three GUI files — `src/gui/main_window.py`, `src/gui/control_panel.py`, and `src/gui/polar_view.py`. No backend logic is touched. Each task is independent and can be committed separately.

**Tech Stack:** PyQt6, Matplotlib (polar embed), hand-written Leaflet HTML (unchanged).

---

## Root-Cause Reference

| # | Symptom | Root Cause | File | Line |
|---|---------|-----------|------|------|
| B1 | Compute button permanently disabled after error | `_on_computation_error` never calls `set_computing_finished()` | `main_window.py` | 672 |
| B2 | Control panel clips off-screen on short displays | `ControlPanel` uses bare `QVBoxLayout` with no `QScrollArea` | `control_panel.py` | 286 |
| B3 | Target Flight Heights: cramped, nested scroll | `QTableWidget` with `setMaximumHeight(150)`, redundant label | `control_panel.py` | 168–174 |
| B4 | Polar OVD title covered when map is maximised | No `setMinimumHeight()` on `PolarView`; splitter has no initial size guard; title `pad=20` clips | `main_window.py:508`, `polar_view.py:118` |
| B5 | QSpinBox arrows invisible / hard-to-click in dark theme | `QSpinBox::up-button` / `::down-button` sub-controls unstyled | `main_window.py` | 416 |
| B6 | Shadow opacity visually shows 55% but renders at 50% | `_shadow_opacity = 0.5` in `MapView.__init__` but slider initialises to 55 | `map_view.py:50`, `control_panel.py:222` |

---

## File Map

| File | What changes |
|------|-------------|
| `src/gui/main_window.py` | B1: add `set_computing_finished()` call; B4: splitter min-sizes; B5: add spinbox sub-control stylesheet |
| `src/gui/control_panel.py` | B2: wrap content in `QScrollArea`; B3: replace `QTableWidget` with checkbox rows; B6: align shadow-opacity default |
| `src/gui/polar_view.py` | B4: reduce title pad, call `tight_layout()` on update, set minimum height |

---

## Task 1 — B1: Fix compute button permanently stuck after error

**Files:**
- Modify: `src/gui/main_window.py:672-676`

The `_on_computation_error` handler resets the progress bar and shows a dialog but never re-enables the COMPUTE button. `set_computing_finished()` is only called by `_on_computation_finished`, which is NOT emitted when the worker hits an exception.

- [ ] **Step 1: Open the file and locate the handler**

Read `src/gui/main_window.py` lines 672-683 to confirm the gap.

- [ ] **Step 2: Add the missing call**

In `src/gui/main_window.py`, change `_on_computation_error` from:

```python
def _on_computation_error(self, error_msg: str):
    """Handle computation error."""
    self.label_status.setText("Error")
    self.progress_bar.setVisible(False)
    QMessageBox.critical(self, "Computation Error", error_msg)
```

to:

```python
def _on_computation_error(self, error_msg: str):
    """Handle computation error."""
    self.label_status.setText("Error")
    self.progress_bar.setVisible(False)
    self.control_panel.set_computing_finished()   # re-enable COMPUTE button
    QMessageBox.critical(self, "Computation Error", error_msg)
```

- [ ] **Step 3: Verify manually**

Run `python main.py`, load a DEM, set Max Range to a value that triggers a fast error (e.g. delete the DEM path via the code temporarily), click COMPUTE. Confirm the button re-enables after the error dialog is dismissed.

- [ ] **Step 4: Commit**

```bash
git add src/gui/main_window.py
git commit -m "fix: re-enable compute button after computation error"
```

---

## Task 2 — B5: Style QSpinBox up/down arrows in dark theme

**Files:**
- Modify: `src/gui/main_window.py` (the `dark_stylesheet` string in `_apply_dark_theme`)

Without styling `QSpinBox::up-button` / `::down-button`, Qt falls back to a platform-native renderer that paints white buttons inside the dark box — making them nearly invisible and very hard to click.

- [ ] **Step 1: Locate the stylesheet block**

In `src/gui/main_window.py`, find the `dark_stylesheet` string that starts around line 340. The existing spinbox rule is:

```python
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
```

- [ ] **Step 2: Append sub-control rules immediately after the spinbox block**

Add these rules inside the `dark_stylesheet` f-string (after the existing spinbox block and before `QGroupBox`):

```python
            QSpinBox::up-button, QDoubleSpinBox::up-button {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 18px;
                background-color: #4a4a5e;
                border-left: 1px solid #555;
                border-bottom: 1px solid #555;
                border-top-right-radius: 3px;
            }}
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 18px;
                background-color: #4a4a5e;
                border-left: 1px solid #555;
                border-bottom-right-radius: 3px;
            }}
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
                background-color: {ACCENT_BLUE};
            }}
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
                image: none;
                width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid #e8e8e8;
            }}
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
                image: none;
                width: 0; height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #e8e8e8;
            }}
```

> **Note:** Qt CSS does not support real CSS triangles via `border` tricks. The `border-*: 5px solid` arrow technique works in some Qt versions but not all. If arrows appear invisible after this change, replace the `::up-arrow` / `::down-arrow` blocks with just `width: 0; height: 0;` and rely on the button background alone for click targets. The critical fix is the `width: 18px` and hover colour so buttons are reachable.

- [ ] **Step 3: Verify visually**

Run `python main.py`. Confirm that all QSpinBox/QDoubleSpinBox widgets (lat, lon, site elevation, mast height, antenna height, max range, beam angles) show clearly visible up/down buttons with hover highlight.

- [ ] **Step 4: Commit**

```bash
git add src/gui/main_window.py
git commit -m "fix: style QSpinBox up/down buttons in dark theme"
```

---

## Task 3 — B2: Wrap control panel in a QScrollArea

**Files:**
- Modify: `src/gui/control_panel.py:286` (`_create_layout`)
- Modify: `src/gui/main_window.py:504` (remove hard `setMaximumWidth` from the outer widget)

Without a scroll area the COMPUTE button and Export buttons are invisible when the window height is < ~900 px (common on laptops).

- [ ] **Step 1: Add QScrollArea import**

In `src/gui/control_panel.py`, change the imports block from:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QSpinBox, QDoubleSpinBox,
    QSlider, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem, QFileDialog,
    QProgressBar, QComboBox, QLineEdit
)
```

to:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QSpinBox, QDoubleSpinBox,
    QSlider, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem, QFileDialog,
    QProgressBar, QComboBox, QLineEdit, QScrollArea
)
```

- [ ] **Step 2: Restructure `_create_layout` to use a scroll area**

Replace the existing `_create_layout` method in `src/gui/control_panel.py` with:

```python
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

    # K-factor layout
    k_layout = QHBoxLayout()
    k_layout.addWidget(self.label_k_factor)
    k_layout.addWidget(self.slider_k_factor)
    k_layout.addWidget(self.label_k_value)
    gr_layout.addLayout(k_layout)

    # Diffraction guard layout
    diff_layout = QHBoxLayout()
    diff_layout.addWidget(self.label_diffraction)
    diff_layout.addWidget(self.slider_diffraction)
    diff_layout.addWidget(self.label_diffraction_value)
    gr_layout.addLayout(diff_layout)

    group_radar.setLayout(gr_layout)
    layout.addWidget(group_radar)

    # === Computation Resolution Group ===
    group_resolution = QGroupBox("Computation Resolution")
    gres_layout = QVBoxLayout()
    res_row = QHBoxLayout()
    res_row.addWidget(self.combo_resolution)
    res_row.addWidget(self.label_resolution_estimate)
    gres_layout.addLayout(res_row)
    group_resolution.setLayout(gres_layout)
    layout.addWidget(group_resolution)

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
    gh_layout.addLayout(self._height_bands_layout)   # replaced table — see Task 4
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
    layout.addWidget(self.label_computing)

    # === Export Buttons ===
    layout.addWidget(self.btn_export_geojson)
    layout.addWidget(self.btn_export_png)

    layout.addStretch()

    scroll.setWidget(inner)
    outer.addWidget(scroll)
```

> **Note:** This references `self._height_bands_layout` which is created in Task 4. Tasks 3 and 4 must be implemented together (Task 4 first).

- [ ] **Step 3: Verify scroll behaviour**

Run `python main.py`, resize the window to ~600 px tall. Confirm the control panel scrolls and the COMPUTE button is always reachable.

- [ ] **Step 4: Commit** (after Task 4 — commit both together)

---

## Task 4 — B3: Replace QTableWidget height bands with inline checkbox rows

**Files:**
- Modify: `src/gui/control_panel.py` — `_create_widgets`, `_populate_height_bands_table`, `_get_selected_height_bands`

The current `QTableWidget` with `setMaximumHeight(150)` creates an awkward nested scrollbar and wastes column space. Replace with five plain `QHBoxLayout` rows — one per band — each containing a `QCheckBox` (with the band name as label), a small colour swatch `QLabel`, and that's it.

- [ ] **Step 1: Add per-band row widgets in `_create_widgets`**

Remove the `QTableWidget` block (lines 168–174) and the `label_heights` widget. Add instead, at the same location in `_create_widgets`:

```python
        # === Height Bands (inline rows, no table) ===
        # Each entry: (height_m, checkbox_widget, enabled_initially)
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
```

- [ ] **Step 2: Remove the old `_populate_height_bands_table` method**

Delete the entire `_populate_height_bands_table` method (lines 255–284 in the original file). It is no longer needed.

- [ ] **Step 3: Rewrite `_get_selected_height_bands`**

Replace the old `_get_selected_height_bands` (which iterated over `table_heights`) with:

```python
def _get_selected_height_bands(self) -> list[float]:
    """Return heights (m) whose checkbox is enabled."""
    return [float(h) for h, cb in self._band_checkboxes.items() if cb.isChecked()]
```

- [ ] **Step 4: Remove `table_heights` references from `_create_widgets`**

Remove these lines from `_create_widgets` (around original line 170):
```python
        self.label_heights = QLabel("Target Flight Heights:")
        self.table_heights = QTableWidget()
        self.table_heights.setColumnCount(3)
        self.table_heights.setHorizontalHeaderLabels(["Enable", "Height (m)", "Color"])
        self.table_heights.setMaximumHeight(150)
        self._populate_height_bands_table()
```
Also remove the import of `QTableWidget` and `QTableWidgetItem` from the import block if they're no longer used elsewhere.

- [ ] **Step 5: Update `_create_layout` / height bands section**

The height bands group in `_create_layout` originally added `self.label_heights` and `self.table_heights`. Replace that block with the reference to `self._height_bands_layout` (already done in Task 3's layout rewrite):
```python
    group_heights = QGroupBox("Target Flight Heights")
    gh_layout = QVBoxLayout()
    gh_layout.addLayout(self._height_bands_layout)
    group_heights.setLayout(gh_layout)
    layout.addWidget(group_heights)
```

- [ ] **Step 6: Verify visually**

Run `python main.py`. The "Target Flight Heights" section should show five checkbox rows with colour swatches and no inner scroll. All five are checked by default. Un-checking one and clicking COMPUTE should exclude that height.

- [ ] **Step 7: Commit Tasks 3 + 4 together**

```bash
git add src/gui/control_panel.py
git commit -m "fix: replace height-bands table with inline rows, wrap panel in QScrollArea"
```

---

## Task 5 — B4: Fix polar OVD title clipped when map is maximised

**Files:**
- Modify: `src/gui/polar_view.py:95-119` and `src/gui/polar_view.py:140-189`
- Modify: `src/gui/main_window.py:508-512` (right splitter setup)

Three changes together eliminate the clipping:
1. Reduce matplotlib title `pad` from 20 to 10.
2. Call `tight_layout()` after drawing so matplotlib auto-trims margins.
3. Set a minimum height (200 px) on the `PolarView` widget so the splitter can't collapse it below legibility.
4. Set initial splitter sizes explicitly so the polar view starts at a reasonable height.

- [ ] **Step 1: Reduce title pad in `_create_initial_plot`**

In `src/gui/polar_view.py`, in `_create_initial_plot`, change:

```python
        self.ax.set_title("Coverage Diagram (OVD)", color=ACCENT_TEXT, pad=20, fontsize=12, fontweight='bold')
```

to:

```python
        self.ax.set_title("Coverage Diagram (OVD)", color=ACCENT_TEXT, pad=8, fontsize=11, fontweight='bold')
        self.fig.tight_layout(pad=0.5)
```

- [ ] **Step 2: Same reduction in `update_data`**

In `update_data`, change:

```python
        self.ax.set_title("Coverage Diagram (OVD)", color=ACCENT_TEXT, pad=20, fontsize=12, fontweight='bold')
```

to:

```python
        self.ax.set_title("Coverage Diagram (OVD)", color=ACCENT_TEXT, pad=8, fontsize=11, fontweight='bold')
```

And at the very end of `update_data`, before `self.canvas.draw()`, add:

```python
        self.fig.tight_layout(pad=0.5)
```

- [ ] **Step 3: Set minimum height on PolarView widget**

At the end of `PolarView._create_widgets` (after `layout.addWidget(self.canvas)`), add:

```python
        self.setMinimumHeight(200)
```

- [ ] **Step 4: Set initial splitter sizes in MainWindow**

In `src/gui/main_window.py`, in `_create_layout`, find the right splitter setup:

```python
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self.map_view)
        right_splitter.addWidget(self.polar_view)
        right_splitter.setStretchFactor(0, 70)
        right_splitter.setStretchFactor(1, 30)
```

Change to:

```python
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self.map_view)
        right_splitter.addWidget(self.polar_view)
        right_splitter.setStretchFactor(0, 70)
        right_splitter.setStretchFactor(1, 30)
        right_splitter.setSizes([600, 280])   # initial pixel split; splitter respects min-height
        right_splitter.setCollapsible(1, False)  # prevent polar view being collapsed to 0
```

- [ ] **Step 5: Verify**

Run `python main.py`. Drag the horizontal splitter all the way up (map maximised). Confirm "Coverage Diagram (OVD)" title remains visible and doesn't overlap the map. The splitter should stop before the polar view disappears entirely.

- [ ] **Step 6: Commit**

```bash
git add src/gui/polar_view.py src/gui/main_window.py
git commit -m "fix: prevent polar OVD title clipping when map is maximised"
```

---

## Task 6 — B6: Align shadow opacity initial value

**Files:**
- Modify: `src/gui/map_view.py:50`

The `QSlider` for shadow opacity initialises at 55 (= 55%) but `MapView._shadow_opacity` initialises at `0.5` (50%). The first render after computation uses the stale map-view value.

- [ ] **Step 1: Change MapView default**

In `src/gui/map_view.py`, change:

```python
        self._shadow_opacity = 0.5            # absolute opacity for shadow polylines (0.0–1.0)
```

to:

```python
        self._shadow_opacity = 0.55           # matches slider default (55) in ControlPanel
```

- [ ] **Step 2: Verify**

Run `python main.py`, load a DEM, compute. Observe shadow zones — opacity should match the 55% slider position without needing to touch the slider first.

- [ ] **Step 3: Commit**

```bash
git add src/gui/map_view.py
git commit -m "fix: align MapView shadow opacity default with control panel slider (55%)"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] B1 — compute button stuck: Task 1
- [x] B2 — control panel scroll: Task 3
- [x] B3 — height bands table: Task 4
- [x] B4 — polar OVD title: Task 5
- [x] B5 — spinbox arrows: Task 2
- [x] B6 — shadow opacity sync: Task 6

**Placeholder scan:** No TBDs. All code blocks are complete and self-contained.

**Type consistency:**
- `self._band_checkboxes` defined in Task 4 Step 1, consumed in `_get_selected_height_bands` (Task 4 Step 3) — consistent.
- `self._height_bands_layout` defined in Task 4 Step 1, consumed in Task 3's `_create_layout` — consistent.
- `right_splitter.setCollapsible(1, False)` — `setCollapsible(index, bool)` is a valid `QSplitter` method in PyQt6.
- `tight_layout(pad=0.5)` — valid Matplotlib `Figure` method.

**Execution order note:** Task 4 must be implemented before Task 3 (Task 3's `_create_layout` references `self._height_bands_layout` which Task 4 creates). All other tasks are independent.

**Regression risk:**
- `_get_selected_height_bands` return type remains `list[float]` — `ComputationWorker` consumes this unchanged.
- Shadow/coverage rendering pipeline unchanged.
- `QScrollArea` wrapping the panel: `setWidgetResizable(True)` ensures the inner widget resizes correctly; `ScrollBarAlwaysOff` on horizontal prevents horizontal scroll that would look odd in a 350 px panel.
