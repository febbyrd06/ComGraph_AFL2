import geopandas as gpd
import pytest
from shapely.geometry import Point

from config import GEOGRAPHIC_CRS
from src.routing_service import (
    RouteComparison,
    RoutingError,
    RoutingService,
    parse_route_response,
    parse_table_response,
)


TABLE_RESPONSE = {
    "code": "Ok",
    "durations": [[420], [300]],
    "distances": [[5200], [4100]],
}
ROUTE_RESPONSE = {
    "code": "Ok",
    "routes": [
        {
            "distance": 4100,
            "duration": 300,
            "geometry": {
                "type": "LineString",
                "coordinates": [[112.7, -7.25], [112.71, -7.24]],
            },
        }
    ],
}


def _stations():
    return gpd.GeoDataFrame(
        {
            "station_id": ["station-a", "station-b"],
            "name": ["Station A", "Station B"],
        },
        geometry=[Point(112.68, -7.25), Point(112.71, -7.24)],
        crs=GEOGRAPHIC_CRS,
    )


def test_table_response_parsing_and_unavailable_station():
    payload = {
        "code": "Ok",
        "durations": [[120], [None]],
        "distances": [[1200], [None]],
    }
    routes = parse_table_response(payload, ["a", "b"], ["A", "B"])
    assert routes[0].success
    assert routes[0].distance_meters == 1200
    assert not routes[1].success
    assert "snap" in routes[1].reason


def test_invalid_or_unavailable_osrm_response_has_clear_error():
    with pytest.raises(RoutingError, match="No route found"):
        parse_table_response({"code": "NoRoute", "message": "No route found"}, ["a"], ["A"])
    with pytest.raises(RoutingError, match="non-numeric"):
        parse_table_response(
            {
                "code": "Ok",
                "durations": [["fast"], [120]],
                "distances": [[1200], [1200]],
            },
            ["a", "b"],
            ["A", "B"],
        )
    with pytest.raises(RoutingError, match="invalid route geometry"):
        parse_route_response({"code": "Ok", "routes": [{"geometry": {"coordinates": []}}]})
    with pytest.raises(RoutingError, match="invalid route record"):
        parse_route_response({"code": "Ok", "routes": [None]})


def test_routing_compares_stations_and_returns_winner_geometry(monkeypatch):
    service = RoutingService()
    responses = iter([TABLE_RESPONSE, ROUTE_RESPONSE])
    requested_urls = []

    def fake_get_json(url):
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(service, "_get_json", fake_get_json)
    comparison = service.compare_routes(_stations(), Point(112.72, -7.23))
    assert "sources=0;1&destinations=2" in requested_urls[0]
    assert comparison.routes[0].duration_seconds == 420
    assert comparison.fastest_station_id == "station-b"
    assert comparison.geometry is not None
    assert comparison.routes[1].distance_meters == 4100
    assert comparison.routes[1].duration_seconds == 300
    assert "not live traffic" in comparison.message


def test_routing_table_query_uses_the_incident_as_its_only_destination(monkeypatch):
    service = RoutingService()
    requested_urls = []
    responses = iter([TABLE_RESPONSE, ROUTE_RESPONSE])

    def fake_get_json(url):
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(service, "_get_json", fake_get_json)
    service.compare_routes(_stations(), Point(112.72, -7.23))
    table_url = requested_urls[0]
    assert "sources=0;1&destinations=2" in table_url
    assert table_url.split("/table/v1/driving/", 1)[1].split("?", 1)[0].endswith(
        "112.720000,-7.230000"
    )


def test_table_response_rejects_wrong_number_of_destination_columns():
    with pytest.raises(RoutingError, match="destinations"):
        parse_table_response(
            {"code": "Ok", "durations": [[120, 130]], "distances": [[1200, 1300]]},
            ["a"],
            ["A"],
        )


def test_osrm_unavailable_is_reported_without_crashing(monkeypatch):
    service = RoutingService()

    def unavailable(_url):
        raise RoutingError("OSRM is unavailable (timeout).")

    monkeypatch.setattr(service, "_get_json", unavailable)
    comparison = service.compare_routes(_stations(), Point(112.72, -7.23))
    assert isinstance(comparison, RouteComparison)
    assert comparison.fastest_station_id is None
    assert len(comparison.routes) == 2
    assert all(not route.success for route in comparison.routes)
    assert "unavailable" in comparison.message


def test_no_stations_returns_empty_comparison():
    empty = gpd.GeoDataFrame(
        {"station_id": [], "name": []},
        geometry=gpd.GeoSeries([], crs=GEOGRAPHIC_CRS),
        crs=GEOGRAPHIC_CRS,
    )
    result = RoutingService().compare_routes(empty, Point(112.72, -7.23))
    assert result.routes == ()
    assert result.fastest_station_id is None
