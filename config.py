from pathlib import Path

from pyproj import CRS


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
MAP_FILE = DATA_DIR / "surabaya_map.gpkg"
STATIONS_FILE = DATA_DIR / "stations.geojson"
SAMPLES_FILE = DATA_DIR / "residential_samples.geojson"
PROJECTED_CRS = CRS.from_epsg(32749)
GEOGRAPHIC_CRS = CRS.from_epsg(4326)
BUFFER_DISTANCES_METERS = (3_000, 5_000)
SAMPLE_POINT_COUNT = 13
SAMPLE_MIN_SEPARATION_METERS = 300
OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_SECONDS = 12
MAX_ROUTING_STATIONS = 30
WEST_SURABAYA_DISTRICTS = (
    "Benowo",
    "Pakal",
    "Asemrowo",
    "Sambikerep",
    "Lakarsantri",
    "Sukomanunggal",
    "Tandes",
)
