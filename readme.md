# Radar Coverage Analysis Tool

A desktop app that shows **where a ground radar can see, and where hills and mountains block it.**

You give it a radar location and a terrain map (a height map of the ground). It works out, for aircraft flying at different heights, how far the radar can see in every direction. The result is drawn as coloured areas on a map, plus a round "polar" chart.

It can also **search an area for the best place to put a radar** (the Top-K Site Finder).

---

## What you need

| Thing | Why |
|-------|-----|
| A Windows, macOS or Linux computer | The app runs on all three |
| **Python 3.11 or 3.12** | The app is written in Python |
| An internet connection | Needed to install the app and to load the background map |
| A terrain file (`.tif`) for your area | The app needs ground heights to work. See [Step 4](#step-4--get-a-terrain-file-for-your-area) |

---

## Step 1 — Install Python

Pick **one** of these two options. If you are unsure, use **Option A**: it handles the map libraries most easily, especially on Windows.

**Option A: Anaconda (recommended)**
1. Download and install Anaconda from https://www.anaconda.com/download. Accept the default settings.
2. Open **Anaconda Prompt** (Windows: Start menu) or a Terminal (macOS/Linux).

**Option B: Plain Python**
1. Download Python 3.12 from https://www.python.org/downloads/.
2. **Windows:** during install, tick **"Add python.exe to PATH"**.
3. Open a terminal (Windows: Command Prompt or PowerShell).

To check that it worked, type:

```bash
python --version
```

You should see something like `Python 3.12.x`.

---

## Step 2 — Download this project

**Easy way (no Git):** on this GitHub page, click the green **Code** button, then **Download ZIP**. Unzip it somewhere you can find it, for example your Desktop.

**With Git:**

```bash
git clone https://github.com/Saroy-ctrl/radar-coverage-tool.git
```

Then move into the project folder in your terminal. Change the path to wherever you put it:

```bash
cd path/to/radar-coverage-tool
```

You are in the right place when the folder contains `main.py`.

---

## Step 3 — Install the required libraries

Run these commands from inside the project folder. This only needs to be done once.

**If you used Anaconda (Option A):**

```bash
conda create -n radar python=3.12 -y
conda activate radar
conda install -c conda-forge rasterio pyproj scipy numpy pandas shapely matplotlib -y
pip install PyQt6 PyQt6-WebEngine
```

> Each time you open a new Anaconda Prompt later, run `conda activate radar` first.

**If you used plain Python (Option B):**

```bash
pip install -r requirements.txt
```

This may take a few minutes.

---

## Step 4 — Get a terrain file for your area

The app needs a **DEM** (Digital Elevation Model): a GeoTIFF (`.tif`) image where each pixel is the ground height in metres. The free **SRTM** data works well.

1. Go to https://srtm.csi.cgiar.org/srtmdata/ (free, no account needed).
2. Choose **GeoTIFF** format, then click the map tile that contains your radar location.
3. Download the `.zip` and unzip it. Inside is a `.tif` file. That is your terrain file.
4. Put it anywhere you like. A `data/dem/` folder inside the project keeps things tidy.

> **Important:** the terrain file should cover the whole area around your radar, out to the **Max Range** you plan to use. Anywhere outside the file is treated as flat sea level, which makes coverage look larger than it really is.

Other sources: [OpenTopography](https://opentopography.org/) or [USGS EarthExplorer](https://earthexplorer.usgs.gov/) (both need a free account).

---

## Step 5 — Start the app

From inside the project folder (with `conda activate radar` done first, if you used Anaconda):

```bash
python main.py
```

A window opens with **settings on the left** and a **map on the right**. The **polar chart** sits below the map.

---

## Step 6 — Compute radar coverage

Work down the left-hand panel from top to bottom:

1. **DEM File:** click **Browse DEM...** and choose your `.tif` terrain file.
2. **Antenna Location:** type the radar's **Latitude** and **Longitude** in decimal degrees. North and East are positive; South and West are negative. Example: `51.13`, `1.32`.
3. **Site Elevation AMSL (m):** the ground height at the radar, in metres above sea level.
4. **Mast Height / Antenna Height (m):** how high the antenna sits above the ground.
5. **Max Instrumented Range (km):** how far the radar can see at most.
6. **Target Flight Heights:** the aircraft heights to check, in metres above the ground. Five are set by default. You can:
   - tick or untick a height to show or hide it,
   - change the number,
   - click the colour box to pick a colour,
   - use **+ Add Band** or **−** to add or remove heights.
7. Click **COMPUTE**.

After a few seconds:
- **Coloured areas** on the map show where an aircraft at each height can be seen.
- **Dark red areas** (the "shadow") show ground hidden behind hills.
- The **polar chart** shows the same coverage as range against compass direction.

### Optional settings

| Setting | What it does | If unsure |
|---------|--------------|-----------|
| **K-Factor** | How much the atmosphere bends radar waves | Leave at **1.333** (normal conditions) |
| **Diffraction Guard Angle** | Extra safety margin over hilltops | Leave at **0.5°** |
| **Beam Elevation Angles** | Limits the radar to look only between a min and max angle. Can leave a "hole" near the radar | Leave at **−90° / 90°** (no limit) |
| **Obstructions** | Load a CSV of buildings or towers to include. See [format below](#obstruction-file-format) | Skip |
| **Display** | Coverage and shadow transparency, and shadow style | Adjust to taste |

### Saving results

- **Export PNG:** saves a picture of the polar chart. To save the map, take a screenshot.
- **Export GeoJSON:** saves the coverage areas as a file for GIS software such as QGIS. This export is still a work in progress, and the saved shapes may not yet match what you see on the map.

---

## Step 7 (optional) — Find the best radar site

The **Top-K Site Finder** tests many possible radar positions inside an area and ranks them by how much of that area each one can see.

1. Load a terrain file first (Step 6, item 1).
2. Under **Top-K Site Finder**, type the corners of the search area into the **Min/Max Lat** and **Min/Max Lon** boxes.
3. Set:
   - **Top N sites:** how many of the best sites to list,
   - **m grid:** the grid step, i.e. the spacing between test positions. Smaller means more sites, which is slower but more thorough,
   - **Target height range:** the aircraft heights to plan for.
   The panel shows roughly how many sites will be tested.
4. Click **Find Top-K Sites**. A progress count appears, and you can stop at any time with **Cancel Search**.
5. Results are listed as `X km² @ N m AGL`: the area covered at the lowest target height. Click **ℹ** for a full explanation.
6. Click **Load** next to any result. This moves the radar there (with its correct ground height) and computes its full coverage straight away.

---

## Obstruction file format

A plain CSV file (you can make one in Excel and choose "Save As → CSV"). Example:

```csv
lat,lon,height_amsl_m,type,description
51.5045,-0.0865,310,building,The Shard
51.5054,-0.0235,235,building,Canary Wharf
```

`height_amsl_m` is the **top** of the object in metres above sea level, not its height above the ground.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python` is not recognised | Python isn't on your PATH. Reinstall and tick **"Add python.exe to PATH"**, or use Anaconda Prompt |
| `ModuleNotFoundError: No module named ...` | Step 3 wasn't completed in this terminal. With Anaconda, run `conda activate radar` first |
| `DLL load failed while importing QtWidgets` (Windows) | PyQt6 install is broken or mismatched. Run `pip install --force-reinstall PyQt6 PyQt6-WebEngine` inside the same environment |
| Map area is blank or grey | No internet connection. The background map loads online |
| **Draw Rectangle on Map** does nothing | Some PyQt6 installs lack the map-to-app link. Type the search-area corners into the boxes instead |
| Coverage looks like a perfect circle | The terrain file doesn't cover your radar location. Check the lat/lon, and that you downloaded the right tile |
| Coverage looks far too big over land | The terrain file is too small for your Max Range. Use a bigger area or a lower range |
| Top-K search is slow | Increase the **m grid** step or make the search area smaller |

---

## How it works (short version)

For each compass direction (every 0.5°), the app samples ground heights outward from the radar every 50 m. It corrects them for the Earth's curvature and for the bending of radar waves (the K-factor). Then it tracks the steepest hill seen so far. An aircraft is visible only if it sits above that line of sight. Repeating this for every direction and every target height gives the coverage shapes.

Based on methods from a BEL (Bharat Electronics Ltd) technical paper by P.K. Gupta and V.K. Gupta, with a layout inspired by the Cambridge Pixel SPx Radar Coverage tool.

---

**Author:** Saanann Roy
