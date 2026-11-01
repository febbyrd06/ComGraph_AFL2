import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

import web_app.app as web_module
from config import GEOGRAPHIC_CRS
from src.routing_service import RouteComparison, StationRoute


@pytest.fixture
def client(monkeypatch):
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
    roads = gpd.GeoDataFrame(
        geometry=[LineString([(112.61, -7.25), (112.79, -7.25)])],
        crs=GEOGRAPHIC_CRS,
    )
    stations = gpd.GeoDataFrame(
        {
            "station_id": ["osm-1"],
            "name": ["Mapped station"],
            "category": ["REAL-OSM"],
            "source": ["OpenStreetMap"],
            "verification_status": ["Not authority-verified"],
        },
        geometry=[Point(112.65, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )
    samples = gpd.GeoDataFrame(
        {
            "sample_id": ["sample-1", "sample-2"],
            "label": ["Synthetic 1", "Synthetic 2"],
            "data_type": ["SYNTHETIC SAMPLE"] * 2,
            "source": ["Generated test sample"] * 2,
        },
        geometry=[Point(112.65, -7.25), Point(112.75, -7.25)],
        crs=GEOGRAPHIC_CRS,
    )

    monkeypatch.setattr(
        web_module,
        "load_local_data",
        lambda: (boundary, roads, stations, samples),
    )
    web_module._load_layers.cache_clear()
    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as test_client:
        yield test_client
    web_module._load_layers.cache_clear()


def test_map_data_endpoint_returns_local_layers_and_baseline_coverage(client):
    response = client.get("/api/map-data")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["stations"]["features"]) == 1
    assert len(payload["samples"]["features"]) == 2
    assert payload["real_station_count"] == 1
    assert payload["proposed_station_count"] == 0
    assert payload["coverage"]["3000"]["covered_samples"] == 1
    assert payload["buffers"]["3000"]["features"][0]["properties"]["radius_meters"] == 3000
    assert "not authority-verified" in payload["station_source_note"]


def test_homepage_and_static_frontend_assets_are_served(client):
    homepage = client.get("/")
    script = client.get("/static/js/map.js")
    stylesheet = client.get("/static/css/style.css")

    assert homepage.status_code == 200
    assert b"Select incident" in homepage.data
    assert script.status_code == 200
    assert b'setMode("incident")' in script.data
    assert stylesheet.status_code == 200


def test_proposed_station_updates_before_after_coverage(client):
    response = client.post(
        "/api/coverage",
        json={"proposed_stations": [{"lon": 112.75, "lat": -7.25}]},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["real_station_count"] == 1
    assert payload["proposed_station_count"] == 1
    assert payload["coverage"]["3000"]["covered_samples"] == 2
    assert {
        feature["properties"]["category"]
        for feature in payload["stations"]["features"]
    } == {"REAL-OSM", "PROPOSED"}


@pytest.mark.parametrize(
    "body",
    [
        {"proposed_stations": [{"lon": 300, "lat": 100}]},
        {"proposed_stations": [{"lon": 113.0, "lat": -7.25}]},
        {"proposed_stations": "not-a-list"},
    ],
)
def test_invalid_or_out_of_study_proposal_is_rejected(client, body):
    response = client.post("/api/coverage", json=body)
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_route_endpoint_returns_comparison_and_route_geometry(client, monkeypatch):
    comparison = RouteComparison(
        routes=(
            StationRoute("osm-1", "Mapped station", 1200, 180, True),
        ),
        fastest_station_id="osm-1",
        geometry=LineString([(112.65, -7.25), (112.66, -7.25)]),
        message="Fastest returned route; not live traffic.",
    )
    monkeypatch.setattr(
        web_module._routing_service,
        "compare_routes",
        lambda stations, incident: comparison,
    )

    response = client.post(
        "/api/route",
        json={
            "incident": {"lon": 112.66, "lat": -7.25},
            "proposed_stations": [],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["fastest_station_id"] == "osm-1"
    assert payload["routes"][0]["success"] is True
    assert payload["geometry"]["type"] == "LineString"


def test_route_endpoint_can_request_geometry_from_a_selected_station(client, monkeypatch):
    seen_station_ids = []
    comparison = RouteComparison(
        routes=(StationRoute("proposed-001", "Proposed station 1", 900, 120, True),),
        fastest_station_id="proposed-001",
        geometry=LineString([(112.75, -7.25), (112.76, -7.25)]),
        message="Selected station route.",
    )

    def compare_routes(stations, incident):
        seen_station_ids.extend(stations["station_id"].astype(str))
        return comparison

    monkeypatch.setattr(web_module._routing_service, "compare_routes", compare_routes)
    response = client.post(
        "/api/route",
        json={
            "incident": {"lon": 112.66, "lat": -7.25},
            "proposed_stations": [{"lon": 112.75, "lat": -7.25}],
            "selected_station_id": "proposed-001",
        },
    )

    assert response.status_code == 200
    assert seen_station_ids == ["proposed-001"]
    assert response.get_json()["fastest_station_id"] == "proposed-001"


def test_route_endpoint_rejects_incident_outside_study_area(client):
    response = client.post(
        "/api/route",
        json={
            "incident": {"lon": 113.0, "lat": -7.25},
            "proposed_stations": [],
        },
    )
    assert response.status_code == 400
    assert "inside" in response.get_json()["error"]


def test_missing_local_layers_return_actionable_api_error(client, monkeypatch):
    from src.data_loader import DataUnavailableError

    monkeypatch.setattr(
        web_module,
        "load_local_data",
        lambda: (_ for _ in ()).throw(DataUnavailableError("Map data is missing; prepare it first.")),
    )
    web_module._load_layers.cache_clear()

    response = client.get("/api/map-data")

    assert response.status_code == 503
    assert "prepare it first" in response.get_json()["error"]
