import json

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from config import (
    GEOGRAPHIC_CRS,
    PROJECTED_CRS,
    SAMPLE_MIN_SEPARATION_METERS,
    WEST_SURABAYA_DISTRICTS,
)
from src import data_loader
from src.data_loader import DataUnavailableError, load_local_data
from src.spatial_analysis import calculate_coverage
from src.station_manager import StationManager


def _samples():
    return gpd.GeoDataFrame(
        {"sample_id": ["sample-1", "sample-2"]},
        geometry=[Point(112.70, -7.25), Point(112.75, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )


def test_proposed_station_updates_coverage_and_can_be_removed():
    real = gpd.GeoDataFrame(
        {
            "station_id": ["osm-1"],
            "name": ["OSM station"],
            "category": ["REAL-OSM"],
            "source": ["OpenStreetMap"],
            "verification_status": ["Unverified"],
        },
        geometry=[Point(112.70, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )
    manager = StationManager(real)
    samples = _samples()
    before = calculate_coverage(samples, manager.all_stations())[3_000]
    proposed = manager.add_proposed(Point(112.75, -7.25))
    after = calculate_coverage(samples, manager.all_stations())[3_000]
    assert proposed.category == "PROPOSED"
    assert proposed.verification_status.startswith("Hypothetical")
    assert manager.real_count == 1
    assert manager.proposed_count == 1
    assert after.covered_samples >= before.covered_samples
    assert manager.remove_proposed(proposed.station_id)
    restored = calculate_coverage(samples, manager.all_stations())[3_000]
    assert manager.proposed_count == 0
    assert restored.covered_samples == before.covered_samples


def test_reset_only_removes_hypothetical_stations():
    real = gpd.GeoDataFrame(
        {"station_id": ["osm-1"], "name": ["Mapped"], "category": ["REAL-OSM"]},
        geometry=[Point(112.70, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )
    manager = StationManager(real)
    manager.add_proposed(Point(112.75, -7.25))
    manager.add_proposed(Point(112.76, -7.25))
    manager.reset_proposed()
    assert manager.real_count == 1
    assert manager.proposed_count == 0
    assert list(manager.all_stations()["category"]) == ["REAL-OSM"]


def test_missing_map_files_raise_actionable_message(tmp_path):
    missing = tmp_path / "missing.gpkg"
    with pytest.raises(DataUnavailableError, match="python -m src.data_loader --download"):
        load_local_data(missing, tmp_path / "stations.geojson", tmp_path / "samples.geojson")


def test_local_loader_accepts_empty_osm_station_layer(tmp_path):
    map_path = tmp_path / "map.gpkg"
    stations_path = tmp_path / "stations.geojson"
    samples_path = tmp_path / "samples.geojson"
    gpd.GeoDataFrame(
        geometry=[
            Polygon(
                [
                    (112.6, -7.3),
                    (112.8, -7.3),
                    (112.8, -7.2),
                    (112.6, -7.2),
                ]
            )
        ],
        crs=GEOGRAPHIC_CRS,
    ).to_file(map_path, layer="boundary", driver="GPKG")
    gpd.GeoDataFrame(
        geometry=[LineString([(112.6, -7.25), (112.8, -7.25)])],
        crs=GEOGRAPHIC_CRS,
    ).to_file(map_path, layer="roads", driver="GPKG", mode="a")
    stations_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )
    gpd.GeoDataFrame(
        {"sample_id": ["sample-1"]},
        geometry=[Point(112.7, -7.25)],
        crs=GEOGRAPHIC_CRS,
    ).to_file(samples_path, driver="GeoJSON")

    boundary, roads, stations, samples = load_local_data(
        map_path,
        stations_path,
        samples_path,
    )
    assert len(boundary) == 1
    assert len(roads) == 1
    assert stations.empty
    assert stations.crs == GEOGRAPHIC_CRS
    assert len(samples) == 1


def test_osm_overpass_failure_is_reported_as_actionable_error(monkeypatch):
    boundary = gpd.GeoDataFrame(
        geometry=[
            Polygon(
                [
                    (112.6, -7.3),
                    (112.8, -7.3),
                    (112.8, -7.2),
                    (112.6, -7.2),
                ]
            )
        ],
        crs=GEOGRAPHIC_CRS,
    )
    monkeypatch.setattr(data_loader.ox, "geocode_to_gdf", lambda _query: boundary)

    def overpass_unavailable(*_args, **_kwargs):
        raise ValueError("HTTP 502")

    monkeypatch.setattr(data_loader.ox, "graph_from_polygon", overpass_unavailable)
    with pytest.raises(DataUnavailableError, match="public Overpass service may be busy"):
        data_loader.download_surabaya_data()


def test_west_surabaya_boundary_uses_explicit_subdistricts(monkeypatch):
    calls = []

    def fake_geocode(query):
        calls.append(query)
        return gpd.GeoDataFrame(
            geometry=[Polygon([(112.6, -7.3), (112.7, -7.3), (112.7, -7.2), (112.6, -7.2)])],
            crs=GEOGRAPHIC_CRS,
        )

    monkeypatch.setattr(data_loader.ox, "geocode_to_gdf", fake_geocode)
    boundary = data_loader._surabaya_boundary(west_only=True)
    assert len(calls) == len(WEST_SURABAYA_DISTRICTS)
    assert calls == [
        f"{district}, Surabaya, East Java, Indonesia"
        for district in WEST_SURABAYA_DISTRICTS
    ]
    assert boundary.iloc[0]["name"] == "Surabaya Barat"
    assert boundary.crs == GEOGRAPHIC_CRS


def test_west_surabaya_boundary_reports_geocoding_failure(monkeypatch):
    def failed_geocode(_query):
        raise data_loader.ox._errors.InsufficientResponseError("Nominatim unavailable")

    monkeypatch.setattr(data_loader.ox, "geocode_to_gdf", failed_geocode)
    with pytest.raises(DataUnavailableError, match="could not look up the boundary"):
        data_loader._surabaya_boundary(west_only=True)


def test_west_only_preparation_clips_existing_roads_and_stations(monkeypatch, tmp_path):
    west_polygon = Polygon(
        [(112.6, -7.3), (112.7, -7.3), (112.7, -7.2), (112.6, -7.2)]
    )
    full_boundary = gpd.GeoDataFrame(
        geometry=[
            Polygon(
                [(112.5, -7.4), (112.8, -7.4), (112.8, -7.1), (112.5, -7.1)]
            )
        ],
        crs=GEOGRAPHIC_CRS,
    )
    roads = gpd.GeoDataFrame(
        geometry=[
            LineString([(112.55, -7.25), (112.75, -7.25)]),
            LineString([(112.72, -7.25), (112.75, -7.25)]),
        ],
        crs=GEOGRAPHIC_CRS,
    )
    stations = gpd.GeoDataFrame(
        {
            "station_id": ["west-station", "east-station"],
            "name": ["West", "Outside"],
            "category": ["REAL-OSM", "REAL-OSM"],
            "source": ["OpenStreetMap", "OpenStreetMap"],
            "verification_status": ["Unverified", "Unverified"],
        },
        geometry=[Point(112.65, -7.25), Point(112.75, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )
    samples = gpd.GeoDataFrame(
        {"sample_id": ["old-sample"]},
        geometry=[Point(112.65, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )
    west_boundary = gpd.GeoDataFrame(
        {"name": ["Surabaya Barat"]},
        geometry=[west_polygon],
        crs=GEOGRAPHIC_CRS,
    )
    monkeypatch.setattr(
        data_loader,
        "load_local_data",
        lambda: (full_boundary, roads, stations, samples),
    )
    monkeypatch.setattr(
        data_loader,
        "_surabaya_boundary",
        lambda west_only: west_boundary,
    )
    monkeypatch.setattr(data_loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_loader, "MAP_FILE", tmp_path / "map.gpkg")
    monkeypatch.setattr(data_loader, "STATIONS_FILE", tmp_path / "stations.geojson")
    monkeypatch.setattr(data_loader, "SAMPLES_FILE", tmp_path / "samples.geojson")

    data_loader.prepare_local_west_surabaya_data()
    saved_boundary = gpd.read_file(tmp_path / "map.gpkg", layer="boundary")
    saved_roads = gpd.read_file(tmp_path / "map.gpkg", layer="roads")
    saved_stations = gpd.read_file(tmp_path / "stations.geojson")
    saved_samples = gpd.read_file(tmp_path / "samples.geojson")
    assert saved_boundary.iloc[0]["name"] == "Surabaya Barat"
    assert len(saved_roads) == 1
    assert list(saved_stations["station_id"]) == ["west-station"]
    assert len(saved_samples) == 13
    assert all(west_polygon.covers(point) for point in saved_samples.geometry)


def test_synthetic_samples_are_deterministic_and_snapped_to_roads():
    projected_boundary = Polygon(
        [
            (-5_000, -5_000),
            (5_000, -5_000),
            (5_000, 5_000),
            (-5_000, 5_000),
        ]
    )
    roads = gpd.GeoDataFrame(
        geometry=[
            LineString([(-4_000, -2_000), (4_000, -2_000)]),
            LineString([(-4_000, 0), (4_000, 0)]),
            LineString([(-4_000, 2_000), (4_000, 2_000)]),
        ],
        crs=PROJECTED_CRS,
    )
    geographic_boundary = (
        gpd.GeoSeries([projected_boundary], crs=PROJECTED_CRS)
        .to_crs(GEOGRAPHIC_CRS)
        .iloc[0]
    )

    first = data_loader._sample_points(geographic_boundary, roads)
    second = data_loader._sample_points(geographic_boundary, roads)
    projected_samples = first.to_crs(PROJECTED_CRS)
    road_union = roads.geometry.union_all()

    assert len(first) == 13
    assert first.geometry.equals(second.geometry)
    assert all(road_union.distance(point) < 0.001 for point in projected_samples.geometry)
    assert all(geographic_boundary.covers(point) for point in first.geometry)
    assert all(
        left.distance(right) >= SAMPLE_MIN_SEPARATION_METERS
        for index, left in enumerate(projected_samples.geometry)
        for right in list(projected_samples.geometry)[index + 1 :]
    )
    assert first["source"].str.contains("on the local OSM road network").all()


def test_sample_generation_fails_explicitly_when_road_layer_is_empty():
    empty_roads = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([], crs=GEOGRAPHIC_CRS),
        crs=GEOGRAPHIC_CRS,
    )
    boundary = Polygon(
        [(112.6, -7.3), (112.8, -7.3), (112.8, -7.2), (112.6, -7.2)]
    )
    with pytest.raises(DataUnavailableError, match="road layer is empty"):
        data_loader._sample_points(boundary, empty_roads)


def test_invalid_proposed_station_location_is_rejected():
    empty_real = gpd.GeoDataFrame(
        {"station_id": [], "name": [], "category": []},
        geometry=gpd.GeoSeries([], crs=GEOGRAPHIC_CRS),
        crs=GEOGRAPHIC_CRS,
    )
    manager = StationManager(empty_real)
    with pytest.raises(ValueError, match="longitude/latitude"):
        manager.add_proposed(Point(300, 100))
