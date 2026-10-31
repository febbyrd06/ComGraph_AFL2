# Surabaya Fire-Station Coverage GIS

A Python GIS analysis notebook and interactive Leaflet web map for fire-station
coverage in Surabaya Barat. Both use shared GeoPandas/Shapely analysis functions
and locally prepared OpenStreetMap layers; the Flask backend provides coverage
and on-demand OSRM route comparisons.

> **Educational use only.** This is not an emergency-dispatch tool. OSM station
> entries may be incomplete or misplaced, OSRM durations do not include live
> traffic, and a route result is only the fastest returned route among the
> successfully queried stations.

## Requirements and setup

Use Python 3.11 or newer. The dependency ranges in `requirements.txt` target
compatible stable GeoPandas 1.x, OSMnx 2.x, Shapely 2.x, PyProj 3.x, Pandas 2.x,
Flask 3.x, and Jupyter releases. The GIS implementation was installed and
checked with Python 3.12.10, GeoPandas 1.2.0, OSMnx 2.1.1, Pandas 2.3.3,
PyProj 3.8.0, and Shapely 2.2.0.

From this project folder in the VS Code terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m src.data_loader --download
```

The default download now uses **Surabaya Barat**, defined for this project as
the combined OSM administrative boundaries of Benowo, Pakal, Asemrowo,
Sambikerep, Lakarsantri, Sukomanunggal, and Tandes. This smaller region keeps
the map more focused and reduces the number of roads and stations involved in
the demonstration. A different subdistrict list can be set in
`config.py`.

The one-time data preparation downloads the study-area boundary, drivable OSM
road geometries, and OSM features tagged `amenity=fire_station`; it writes
`data\surabaya_map.gpkg`, `data\stations.geojson`, and
`data\residential_samples.geojson`. Start the app again later without
downloading the map. A network connection is required to prepare the local
layers and to request OSRM routes, but not to display/analyze already prepared
map data. The public Overpass, Nominatim, and OSRM demo services can be slow,
unavailable, or rate-limited; follow their usage policies.

If you already downloaded the full-city map, switch to Surabaya Barat without
downloading the road network again:

```powershell
python -m src.data_loader --west-only
```

This reuses and clips the existing local roads and OSM station data, refreshes
the 13 synthetic sample points for the smaller area, and uses Nominatim to
retrieve the seven subdistrict boundaries. It does not request road data from
the busy Overpass service. To explicitly download the full city again, run
`python -m src.data_loader --full-city`.

If local layers are missing or invalid, the web API and notebook report the
problem instead of presenting a blank map as if it were real geographic data.

## Analysis and data-quality notes

- Geographic inputs use EPSG:4326. Buffer calculations use EPSG:32749 (UTM zone
  49S), suitable for Surabaya's approximately 112.7° E, 7.3° S location.
- For the western-only analysis, the union of the seven named administrative
  subdistrict boundaries defines the study area; samples, mapped stations, and
  locally saved roads are limited to that boundary.
- 3,000 m and 5,000 m station buffers are planar, straight-line distances in
  the projected CRS. They are **not** road-network travel distances or response
  time guarantees.
- The downloader generates 13 reproducible synthetic points directly on the
  local OSM drivable road network, spaced at least 300 m apart. This avoids
  sample dots floating far from roads, but these points are **not actual
  residences or addresses**: no authoritative, lawful residential-address
  dataset is included.
- To keep the Overpass request smaller, roads and station features are queried
  inside a 50 m simplification of the boundary. The full boundary geometry is
  still saved and used for sample generation and the map outline.
- Mapped OSM station features retain their OSM-derived source and are labeled
  as requiring verification. Zero mapped stations is a valid result; proposed
  stations remain separate and are never presented as existing facilities.
- OSRM route duration is an estimate without live traffic. Unreachable or
  unsnappable stations are excluded; the UI reports the returned route
  availability and the fastest result only among successfully queried
  candidates. The nearest 30 stations are queried if a local dataset contains
  more than 30, limiting requests to the public demo server.
- OSM data attribution: © OpenStreetMap contributors
  (https://www.openstreetmap.org/copyright). Retrieved data is subject to the
  Open Database License.

## Tests

```powershell
python -m pytest
```

The tests cover the Surabaya projected CRS and buffer units, known coverage
counts, route response/error handling without network calls, hypothetical
station add/remove/reset behavior, and missing-data errors.

## Jupyter Notebook

The reproducible analysis notebook is
`notebook\analisis_jangkauan_pos_pemadam.ipynb`. It loads the same local GIS
layers and calls the shared analysis functions in `src`; it does not silently
replace missing real-data layers with synthetic data. The 13 sample points
remain explicitly synthetic, and mapped OSM stations are not authority-verified.

Install the requirements above, then open the notebook in VS Code and select
the project's `.venv` Python interpreter. Alternatively, from this project
folder run:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
jupyter notebook notebook\analisis_jangkauan_pos_pemadam.ipynb
```

In Google Colab, first clone or upload the project, including `src\` and `data\`,
set the project root as the working directory, and install its
`requirements.txt`. Colab cannot import the shared modules or read the local
layers if only the `.ipynb` file is uploaded. OSRM routing in the notebook is
opt-in; enable its example cell explicitly to make public API requests.

## Local web map

The Leaflet web app shares the Python analysis and routing modules used by the
notebook. Install the project requirements, ensure the local
GIS layers exist, then start the Flask server from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m web_app.app
```

Open `http://127.0.0.1:5000`. The app uses locally prepared roads, boundary,
station, and sample layers; OSM raster tiles and optional OSRM routes require an
internet connection. If local data is missing, the page reports that rather
than presenting an empty map as valid analysis. Proposed stations are held in
the current browser scenario and remain distinct from OSM-mapped entries.
