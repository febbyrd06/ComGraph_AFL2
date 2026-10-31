from __future__ import annotations

import argparse
import bisect
import json
import random
from pathlib import Path
from typing import Any

import geopandas as gpd
import osmnx as ox
from pyogrio.errors import DataLayerError, DataSourceError
from requests.exceptions import RequestException
from shapely.geometry import Point
from shapely.ops import unary_union

from config import (
    DATA_DIR,
    GEOGRAPHIC_CRS,
    MAP_FILE,
    PROJECTED_CRS,
    SAMPLE_MIN_SEPARATION_METERS,
    SAMPLE_POINT_COUNT,
    SAMPLES_FILE,
    STATIONS_FILE,
    WEST_SURABAYA_DISTRICTS,
)


class DataUnavailableError(RuntimeError):
    """Raised when the required prepared geographic data is missing or invalid."""


def _validate_point_layer(frame: gpd.GeoDataFrame, path: Path) -> gpd.GeoDataFrame:
    if frame.empty:
        raise DataUnavailableError(f"{path.name} contains no features.")
    if frame.crs is None:
        raise DataUnavailableError(f"{path.name} has no declared coordinate reference system.")
    if not frame.geometry.geom_type.isin(["Point"]).all():
        raise DataUnavailableError(f"{path.name} must contain only point geometries.")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise DataUnavailableError(f"{path.name} contains an empty geometry.")
    return frame.to_crs(GEOGRAPHIC_CRS)


def load_local_data(
    map_path: Path = MAP_FILE,
    stations_path: Path = STATIONS_FILE,
    samples_path: Path = SAMPLES_FILE,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load the locally prepared boundary, roads, stations, and synthetic samples."""
    for path in (map_path, stations_path, samples_path):
        if not path.is_file():
            raise DataUnavailableError(
                f"Required map data is missing: {path.name}. "
                "Run `python -m src.data_loader --download` to prepare it."
            )

    try:
        boundary = gpd.read_file(map_path, layer="boundary")
        roads = gpd.read_file(map_path, layer="roads")
        stations = gpd.read_file(stations_path)
        samples = gpd.read_file(samples_path)
    except (OSError, ValueError, DataLayerError, DataSourceError) as exc:
        raise DataUnavailableError(f"Could not read the prepared GIS files: {exc}") from exc

    if boundary.empty or roads.empty:
        raise DataUnavailableError(
            "The local map has an empty boundary or road layer. "
            "Run `python -m src.data_loader --download` again."
        )
    if boundary.crs is None or roads.crs is None:
        raise DataUnavailableError("The local boundary and road layers must declare a CRS.")
    if stations.empty:
        stations = gpd.GeoDataFrame(
            {
                "station_id": [],
                "name": [],
                "category": [],
                "source": [],
                "verification_status": [],
                "geometry": [],
            },
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
    else:
        stations = _validate_point_layer(stations, stations_path)
    samples = _validate_point_layer(samples, samples_path)
    return (
        boundary.to_crs(GEOGRAPHIC_CRS),
        roads.to_crs(GEOGRAPHIC_CRS),
        stations,
        samples,
    )

def _sample_points(
    polygon: Any,
    roads: gpd.GeoDataFrame,
    count: int = SAMPLE_POINT_COUNT,
) -> gpd.GeoDataFrame:
    if count <= 0:
        raise ValueError("The number of sample points must be greater than zero.")
    if roads.empty:
        raise DataUnavailableError("Cannot create residential samples because the road layer is empty.")
    if roads.crs is None:
        raise DataUnavailableError("Cannot create road-aligned samples because the road layer has no CRS.")

    rng = random.Random(2026)
    boundary = gpd.GeoSeries([polygon], crs=GEOGRAPHIC_CRS).to_crs(PROJECTED_CRS).iloc[0]
    projected_roads = roads.to_crs(PROJECTED_CRS)
    segments = []
    cumulative_lengths = []
    total_length = 0.0
    for geometry in projected_roads.geometry:
        if geometry is None or geometry.is_empty:
            continue
        parts = geometry.geoms if geometry.geom_type == "MultiLineString" else (geometry,)
        for segment in parts:
            if segment.geom_type != "LineString" or segment.length <= 0:
                continue
            total_length += segment.length
            segments.append(segment)
            cumulative_lengths.append(total_length)
    if not segments:
        raise DataUnavailableError("Cannot create residential samples because the road layer has no lines.")

    points: list[Point] = []
    max_attempts = count * 10_000
    for _ in range(max_attempts):
        distance_along_network = rng.random() * total_length
        segment_index = bisect.bisect_left(cumulative_lengths, distance_along_network)
        segment_start = cumulative_lengths[segment_index - 1] if segment_index else 0.0
        segment = segments[segment_index]
        candidate = segment.interpolate(distance_along_network - segment_start)
        if not boundary.covers(candidate):
            continue
        if any(
            candidate.distance(existing) < SAMPLE_MIN_SEPARATION_METERS
            for existing in points
        ):
            continue
        points.append(candidate)
        if len(points) == count:
            break
    if len(points) != count:
        raise DataUnavailableError(
            f"Could not generate {count} sufficiently spaced synthetic samples on the "
            "study-area road network. Check the road layer or reduce the sample count."
        )
    return gpd.GeoDataFrame(
        {
            "sample_id": [f"sample-{index:02d}" for index in range(1, count + 1)],
            "label": [f"Synthetic road sample {index:02d}" for index in range(1, count + 1)],
            "data_type": ["SYNTHETIC SAMPLE"] * count,
            "source": ["Generated on the local OSM road network for educational analysis"] * count,
        },
        geometry=gpd.GeoSeries(points, crs=PROJECTED_CRS).to_crs(GEOGRAPHIC_CRS),
        crs=GEOGRAPHIC_CRS,
    )


def _station_layer(features: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    columns = [
        "station_id",
        "name",
        "category",
        "source",
        "verification_status",
        "geometry",
    ]
    records: list[dict[str, Any]] = []
    if not features.empty:
        projected = features.to_crs(PROJECTED_CRS)
        for index, row in projected.iterrows():
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                continue
            point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
            element_id = "-".join(str(part) for part in index) if isinstance(index, tuple) else str(index)
            name = row.get("name")
            if not isinstance(name, str) or not name.strip():
                name = f"OSM fire station {element_id}"
            records.append(
                {
                    "station_id": f"osm-{element_id}",
                    "name": name.strip(),
                    "category": "REAL-OSM",
                    "source": "OpenStreetMap (amenity=fire_station)",
                    "verification_status": "OSM-mapped; verify with the relevant authority",
                    "geometry": point,
                }
            )
    stations = gpd.GeoDataFrame(records, columns=columns, geometry="geometry", crs=PROJECTED_CRS)
    return stations.to_crs(GEOGRAPHIC_CRS)


def _surabaya_boundary(west_only: bool) -> gpd.GeoDataFrame:
    if not west_only:
        print("Looking up the full Surabaya administrative boundary in OpenStreetMap...")
        try:
            boundary = ox.geocode_to_gdf("Surabaya, East Java, Indonesia")
        except (ValueError, RequestException) as exc:
            raise DataUnavailableError(
                f"OpenStreetMap could not look up the Surabaya boundary: {exc}"
            ) from exc
        if boundary.empty:
            raise DataUnavailableError("OpenStreetMap returned no Surabaya boundary.")
        return boundary.to_crs(GEOGRAPHIC_CRS)

    district_geometries = []
    for district in WEST_SURABAYA_DISTRICTS:
        query = f"{district}, Surabaya, East Java, Indonesia"
        print(f"Looking up the Surabaya Barat study-area boundary: {district}...")
        try:
            result = ox.geocode_to_gdf(query)
        except (ValueError, RequestException) as exc:
            raise DataUnavailableError(
                f"OpenStreetMap could not look up the boundary for {district}: {exc}"
            ) from exc
        if result.empty:
            raise DataUnavailableError(
                f"OpenStreetMap returned no boundary for the Surabaya Barat subdistrict {district}."
            )
        district_geometries.append(result.to_crs(GEOGRAPHIC_CRS).geometry.iloc[0])

    polygon = unary_union(district_geometries)
    if polygon.is_empty or not polygon.is_valid:
        raise DataUnavailableError("The combined Surabaya Barat study-area boundary is invalid.")
    return gpd.GeoDataFrame(
        {"name": ["Surabaya Barat"], "source": ["OpenStreetMap administrative subdistricts"]},
        geometry=[polygon],
        crs=GEOGRAPHIC_CRS,
    )


def _write_layers(
    boundary: gpd.GeoDataFrame,
    roads: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
    samples: gpd.GeoDataFrame,
) -> None:
    temporary_map = MAP_FILE.with_name("surabaya_map_build.gpkg")
    if temporary_map.exists():
        temporary_map.unlink()
    boundary.to_file(temporary_map, layer="boundary", driver="GPKG")
    roads.to_file(temporary_map, layer="roads", driver="GPKG", mode="a")
    temporary_map.replace(MAP_FILE)
    for frame, path in ((stations, STATIONS_FILE), (samples, SAMPLES_FILE)):
        if frame.empty:
            path.write_text(
                json.dumps({"type": "FeatureCollection", "features": []}, indent=2),
                encoding="utf-8",
            )
        else:
            frame.to_file(path, driver="GeoJSON")


def prepare_local_west_surabaya_data() -> None:
    """Clip an existing full-city dataset to Surabaya Barat without Overpass."""
    boundary, roads, stations, _ = load_local_data()
    west_boundary = _surabaya_boundary(west_only=True)
    west_polygon = unary_union(west_boundary.geometry)
    if west_polygon.is_empty or not west_polygon.is_valid:
        raise DataUnavailableError("The Surabaya Barat study-area boundary is invalid.")

    roads_projected = roads.to_crs(PROJECTED_CRS)
    west_projected = west_boundary.to_crs(PROJECTED_CRS)
    clipped_roads = gpd.clip(roads_projected, west_projected)
    clipped_roads = clipped_roads[
        clipped_roads.geometry.notna()
        & ~clipped_roads.geometry.is_empty
        & clipped_roads.geometry.geom_type.isin(["LineString", "MultiLineString"])
    ].copy()
    if clipped_roads.empty:
        raise DataUnavailableError(
            "The local road layer contains no roads in the Surabaya Barat study area."
        )
    clipped_roads.geometry = clipped_roads.geometry.simplify(12, preserve_topology=True)
    clipped_roads = clipped_roads.to_crs(GEOGRAPHIC_CRS).reset_index(drop=True)
    west_stations = stations.loc[stations.geometry.intersects(west_polygon)].copy()
    west_samples = _sample_points(west_polygon, clipped_roads)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_layers(west_boundary, clipped_roads, west_stations, west_samples)
    print(
        "Updated the local map to Surabaya Barat using the existing road download: "
        f"{len(WEST_SURABAYA_DISTRICTS)} subdistricts, {len(clipped_roads)} road segments, "
        f"{len(west_stations)} OSM-mapped stations, and {len(west_samples)} synthetic samples."
    )


def download_surabaya_data(west_only: bool = True) -> None:
    """Download and save either Surabaya Barat or the full city from OSM."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.log_console = True

    boundary = _surabaya_boundary(west_only)
    polygon = unary_union(boundary.geometry)
    if polygon.is_empty or not polygon.is_valid:
        raise DataUnavailableError("The returned Surabaya boundary geometry is invalid.")
    query_polygon = (
        gpd.GeoSeries([polygon], crs=GEOGRAPHIC_CRS)
        .to_crs(PROJECTED_CRS)
        .geometry.simplify(50, preserve_topology=True)
        .to_crs(GEOGRAPHIC_CRS)
        .iloc[0]
    )

    print("Downloading the drivable OpenStreetMap road network (this may take a while)...")
    try:
        graph = ox.graph_from_polygon(query_polygon, network_type="drive", simplify=True)
    except (ValueError, RequestException) as exc:
        raise DataUnavailableError(
            "OpenStreetMap could not provide the road network. The public Overpass service "
            "may be busy; check the network connection and retry `python -m src.data_loader --download`."
        ) from exc
    _, edges = ox.graph_to_gdfs(graph, nodes=True, edges=True)
    if edges.empty:
        raise DataUnavailableError("OpenStreetMap returned no road geometries.")
    roads = edges[["geometry"]].to_crs(PROJECTED_CRS)
    roads.geometry = roads.geometry.simplify(12, preserve_topology=True)
    roads = roads.to_crs(GEOGRAPHIC_CRS)
    roads = roads[roads.geometry.notna() & ~roads.geometry.is_empty].reset_index(drop=True)

    print("Searching OpenStreetMap for tagged fire stations...")
    try:
        features = ox.features_from_polygon(query_polygon, tags={"amenity": "fire_station"})
    except (ValueError, RequestException) as exc:
        raise DataUnavailableError(
            "OpenStreetMap could not provide fire-station features. Retry the data download later."
        ) from exc
    stations = _station_layer(features)
    samples = _sample_points(polygon, roads)

    _write_layers(boundary, roads, stations, samples)
    print(
        f"Saved local {'Surabaya Barat' if west_only else 'full Surabaya'} map: "
        f"{len(roads)} road segments, "
        f"{len(stations)} OSM-mapped fire stations, and {len(samples)} synthetic samples."
    )
    print("Mapped stations are not authority-verified. Check their locations before interpreting results.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare local Surabaya OpenStreetMap layers.")
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument(
        "--download",
        action="store_true",
        help="download the Surabaya Barat study area, roads, OSM stations, and synthetic samples",
    )
    options.add_argument(
        "--west-only",
        action="store_true",
        help="clip an existing full-city map to Surabaya Barat without requesting Overpass",
    )
    options.add_argument(
        "--full-city",
        action="store_true",
        help="download the full Surabaya city boundary, roads, OSM stations, and samples",
    )
    args = parser.parse_args()
    try:
        if args.west_only:
            prepare_local_west_surabaya_data()
        else:
            download_surabaya_data(west_only=not args.full_city)
    except DataUnavailableError as exc:
        parser.exit(status=1, message=f"Data preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
