from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

import geopandas as gpd
from flask import Flask, jsonify, render_template, request
from shapely.geometry import Point, mapping
from shapely.ops import unary_union

from config import (
    GEOGRAPHIC_CRS,
    MAX_ROUTING_STATIONS,
    PROJECTED_CRS,
)
from src.data_loader import DataUnavailableError, load_local_data
from src.routing_service import RoutingService
from src.spatial_analysis import calculate_coverage, station_buffer_geometries
from src.station_manager import StationManager


class RequestValidationError(ValueError):
    """A user-supplied map coordinate or scenario is invalid."""


app = Flask(__name__)
_routing_service = RoutingService()


@lru_cache(maxsize=1)
def _load_layers() -> tuple[gpd.GeoDataFrame, ...]:
    return load_local_data()


def _feature_collection(frame: gpd.GeoDataFrame) -> dict[str, Any]:
    return json.loads(frame.to_json(drop_id=True))


def _json_body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise RequestValidationError("Request body must be a JSON object.")
    return payload


def _coordinate(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestValidationError(f"{label} must be a number.")
    coordinate = float(value)
    if not math.isfinite(coordinate):
        raise RequestValidationError(f"{label} must be finite.")
    return coordinate


def _point_from_pair(lon: Any, lat: Any, label: str) -> Point:
    longitude = _coordinate(lon, f"{label} longitude")
    latitude = _coordinate(lat, f"{label} latitude")
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise RequestValidationError(f"{label} must use valid longitude/latitude coordinates.")
    return Point(longitude, latitude)


def _parse_proposals(payload: dict[str, Any]) -> list[Point]:
    proposals = payload.get("proposed_stations", [])
    if not isinstance(proposals, list):
        raise RequestValidationError("proposed_stations must be a list.")
    if len(proposals) > MAX_ROUTING_STATIONS:
        raise RequestValidationError(
            f"A scenario can contain at most {MAX_ROUTING_STATIONS} proposed stations."
        )

    boundary, _, _, _ = _load_layers()
    study_area = unary_union(boundary.geometry)
    points = []
    for index, proposal in enumerate(proposals, start=1):
        if not isinstance(proposal, dict):
            raise RequestValidationError(f"Proposed station {index} must be an object.")
        point = _point_from_pair(
            proposal.get("lon"),
            proposal.get("lat"),
            f"Proposed station {index}",
        )
        if not study_area.covers(point):
            raise RequestValidationError(
                f"Proposed station {index} must be inside the Surabaya Barat study area."
            )
        points.append(point)
    return points


def _scenario_layers(
    stations: gpd.GeoDataFrame,
    proposed_points: list[Point],
) -> tuple[StationManager, gpd.GeoDataFrame]:
    manager = StationManager(stations)
    for point in proposed_points:
        manager.add_proposed(point)
    return manager, manager.all_stations()


def _analysis_payload(
    samples: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    metrics = calculate_coverage(samples, stations)
    coverage = {
        str(radius): {
            "radius_meters": metric.radius_meters,
            "total_samples": metric.total_samples,
            "covered_samples": metric.covered_samples,
            "uncovered_samples": metric.uncovered_samples,
            "percentage": metric.percentage,
        }
        for radius, metric in metrics.items()
    }

    buffers: dict[str, dict[str, Any]] = {}
    for radius in (3_000, 5_000):
        buffer_frame = gpd.GeoDataFrame(
            stations[["station_id", "name", "category"]].copy(),
            geometry=station_buffer_geometries(stations, radius),
            crs=PROJECTED_CRS,
        )
        buffer_frame["radius_meters"] = radius
        buffers[str(radius)] = _feature_collection(
            buffer_frame.to_crs(GEOGRAPHIC_CRS)
        )
    return coverage, buffers


@app.errorhandler(DataUnavailableError)
def handle_missing_data(error: DataUnavailableError):
    return jsonify(error=str(error)), 503


@app.errorhandler(RequestValidationError)
def handle_invalid_request(error: RequestValidationError):
    return jsonify(error=str(error)), 400


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/map-data")
def map_data():
    boundary, roads, stations, samples = _load_layers()
    manager, all_stations = _scenario_layers(stations, [])
    coverage, buffers = _analysis_payload(samples, all_stations)
    return jsonify(
        {
            "boundary": _feature_collection(boundary),
            "roads": _feature_collection(roads),
            "stations": _feature_collection(all_stations),
            "samples": _feature_collection(samples),
            "buffers": buffers,
            "coverage": coverage,
            "real_station_count": manager.real_count,
            "proposed_station_count": manager.proposed_count,
            "station_source_note": (
                "OSM-mapped entries are not authority-verified; proposed stations are hypothetical."
            ),
            "sample_source_note": "Residential samples are synthetic road-aligned demonstration points.",
        }
    )


@app.post("/api/coverage")
def coverage():
    payload = _json_body()
    boundary, _, stations, samples = _load_layers()
    proposed_points = _parse_proposals(payload)
    _, all_stations = _scenario_layers(stations, proposed_points)
    analysis, buffers = _analysis_payload(samples, all_stations)
    return jsonify(
        {
            "coverage": analysis,
            "buffers": buffers,
            "stations": _feature_collection(all_stations),
            "real_station_count": len(stations),
            "proposed_station_count": len(proposed_points),
        }
    )


@app.post("/api/route")
def route():
    payload = _json_body()
    boundary, _, stations, samples = _load_layers()
    proposed_points = _parse_proposals(payload)

    incident_data = payload.get("incident")
    if not isinstance(incident_data, dict):
        raise RequestValidationError("incident must contain longitude and latitude.")
    incident = _point_from_pair(
        incident_data.get("lon"),
        incident_data.get("lat"),
        "Incident",
    )
    if not unary_union(boundary.geometry).covers(incident):
        raise RequestValidationError("The incident must be inside the Surabaya Barat study area.")

    _, all_stations = _scenario_layers(stations, proposed_points)
    selected_station_id = payload.get("selected_station_id")
    routing_stations = all_stations
    if selected_station_id is not None:
        if not isinstance(selected_station_id, str):
            raise RequestValidationError("selected_station_id must be a station identifier.")
        matches = all_stations["station_id"].astype(str) == selected_station_id
        if not matches.any():
            raise RequestValidationError("The selected station is not available in this scenario.")
        routing_stations = all_stations.loc[matches]

    comparison = _routing_service.compare_routes(routing_stations, incident)
    routes = [
        {
            "station_id": result.station_id,
            "station_name": result.station_name,
            "distance_meters": result.distance_meters,
            "duration_seconds": result.duration_seconds,
            "success": result.success,
            "reason": result.reason,
        }
        for result in comparison.routes
    ]
    return jsonify(
        {
            "routes": routes,
            "fastest_station_id": comparison.fastest_station_id,
            "geometry": mapping(comparison.geometry) if comparison.geometry is not None else None,
            "message": comparison.message,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
