from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

import geopandas as gpd
from shapely.geometry import LineString, Point

from config import (
    GEOGRAPHIC_CRS,
    MAX_ROUTING_STATIONS,
    OSRM_BASE_URL,
    OSRM_TIMEOUT_SECONDS,
    PROJECTED_CRS,
)


class RoutingError(RuntimeError):
    """A clear, user-displayable OSRM service or response error."""


@dataclass(frozen=True)
class StationRoute:
    station_id: str
    station_name: str
    distance_meters: float | None
    duration_seconds: float | None
    success: bool
    reason: str | None = None


@dataclass(frozen=True)
class RouteComparison:
    routes: tuple[StationRoute, ...]
    fastest_station_id: str | None
    geometry: LineString | None
    message: str


def parse_table_response(
    payload: dict,
    station_ids: list[str],
    station_names: list[str],
) -> tuple[StationRoute, ...]:
    if payload.get("code") != "Ok":
        raise RoutingError(f"OSRM table request failed: {payload.get('message', payload.get('code'))}")
    durations = payload.get("durations")
    distances = payload.get("distances")
    if not isinstance(durations, list) or not durations:
        raise RoutingError("OSRM returned no route-duration matrix.")
    if not isinstance(distances, list) or not distances:
        raise RoutingError("OSRM returned no route-distance matrix.")
    if len(station_names) != len(station_ids):
        raise RoutingError("The station identifiers and names are inconsistent.")
    if len(durations) != len(station_ids) or len(distances) != len(station_ids):
        raise RoutingError("OSRM returned an unexpected number of station rows.")
    if any(not isinstance(row, list) or len(row) != 1 for row in durations):
        raise RoutingError("OSRM returned an unexpected number of route-duration destinations.")
    if any(not isinstance(row, list) or len(row) != 1 for row in distances):
        raise RoutingError("OSRM returned an unexpected number of route-distance destinations.")

    routes: list[StationRoute] = []
    for index, station_id in enumerate(station_ids):
        duration = durations[index][0]
        distance = distances[index][0]
        if duration is None or distance is None:
            routes.append(
                StationRoute(
                    station_id=station_id,
                    station_name=station_names[index],
                    distance_meters=None,
                    duration_seconds=None,
                    success=False,
                    reason="No road route returned; the station may not snap to the road network.",
                )
            )
        else:
            try:
                duration = float(duration)
                distance = float(distance)
            except (TypeError, ValueError) as exc:
                raise RoutingError("OSRM returned a non-numeric route distance or duration.") from exc
            if (
                not math.isfinite(duration)
                or not math.isfinite(distance)
                or duration < 0
                or distance < 0
            ):
                raise RoutingError("OSRM returned an invalid route distance or duration.")
            routes.append(
                StationRoute(
                    station_id=station_id,
                    station_name=station_names[index],
                    distance_meters=distance,
                    duration_seconds=duration,
                    success=True,
                )
            )
    return tuple(routes)


def parse_route_response(payload: dict) -> tuple[LineString, float, float]:
    if payload.get("code") != "Ok":
        raise RoutingError(f"OSRM route request failed: {payload.get('message', payload.get('code'))}")
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise RoutingError("OSRM returned no route geometry.")
    route = routes[0]
    if not isinstance(route, dict):
        raise RoutingError("OSRM returned an invalid route record.")
    geometry_payload = route.get("geometry")
    if not isinstance(geometry_payload, dict):
        raise RoutingError("OSRM returned invalid route geometry.")
    coordinates = geometry_payload.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise RoutingError("OSRM returned invalid route geometry.")
    try:
        geometry = LineString(coordinates)
        distance = float(route["distance"])
        duration = float(route["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RoutingError(f"OSRM returned an incomplete route: {exc}") from exc
    if (
        geometry.is_empty
        or not geometry.is_valid
        or not math.isfinite(distance)
        or not math.isfinite(duration)
        or distance < 0
        or duration < 0
    ):
        raise RoutingError("OSRM returned an invalid route line.")
    return geometry, distance, duration


class RoutingService:
    """Compare station routes with OSRM Table, then fetch one winner's geometry."""

    def __init__(
        self,
        base_url: str = OSRM_BASE_URL,
        timeout: float = OSRM_TIMEOUT_SECONDS,
        max_stations: int = MAX_ROUTING_STATIONS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_stations = max_stations
        self._cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()

    def _get_json(self, url: str) -> dict:
        with self._cache_lock:
            cached = self._cache.get(url)
        if cached is not None:
            return cached
        request = Request(url, headers={"User-Agent": "SurabayaFireCoverageGIS/1.0 (educational project)"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RoutingError(f"OSRM is unavailable or returned invalid data: {exc}") from exc
        if not isinstance(payload, dict):
            raise RoutingError("OSRM returned a response in an unexpected format.")
        with self._cache_lock:
            if len(self._cache) >= 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = payload
        return payload

    def compare_routes(
        self,
        stations: gpd.GeoDataFrame,
        incident: Point,
    ) -> RouteComparison:
        if stations.crs is None:
            raise RoutingError("Station data has no declared coordinate reference system.")
        if not isinstance(incident, Point) or incident.is_empty:
            raise RoutingError("The incident location is invalid.")
        if not (-180 <= incident.x <= 180 and -90 <= incident.y <= 90):
            raise RoutingError("The incident must be specified in longitude/latitude.")
        if stations.empty:
            return RouteComparison((), None, None, "No stations are available for routing.")

        candidates = stations.to_crs(GEOGRAPHIC_CRS)
        if len(candidates) > self.max_stations:
            projected_incident = gpd.GeoSeries([incident], crs=GEOGRAPHIC_CRS).to_crs(PROJECTED_CRS).iloc[0]
            projected_stations = candidates.to_crs(PROJECTED_CRS)
            candidates = candidates.loc[
                (projected_stations.geometry.distance(projected_incident))
                .sort_values()
                .head(self.max_stations)
                .index
            ]
        station_ids = candidates["station_id"].astype(str).tolist()
        station_names = candidates["name"].astype(str).tolist()
        coords = [
            f"{point.x:.6f},{point.y:.6f}"
            for point in candidates.geometry
        ]
        coords.append(f"{incident.x:.6f},{incident.y:.6f}")
        source_indexes = ";".join(str(index) for index in range(len(station_ids)))
        table_url = (
            f"{self.base_url}/table/v1/driving/{';'.join(coords)}"
            f"?sources={source_indexes}&destinations={len(station_ids)}"
            "&annotations=distance,duration"
        )
        try:
            routes = parse_table_response(
                self._get_json(table_url),
                station_ids,
                station_names,
            )
        except RoutingError as exc:
            return RouteComparison(
                routes=tuple(
                    StationRoute(
                        station_id=station_id,
                        station_name=name,
                        distance_meters=None,
                        duration_seconds=None,
                        success=False,
                        reason=str(exc),
                    )
                    for station_id, name in zip(station_ids, station_names)
                ),
                fastest_station_id=None,
                geometry=None,
                message=str(exc),
            )

        available = [route for route in routes if route.success]
        if not available:
            return RouteComparison(
                routes=routes,
                fastest_station_id=None,
                geometry=None,
                message="No station returned a usable road route.",
            )
        winner = min(available, key=lambda route: route.duration_seconds or float("inf"))
        winner_index = station_ids.index(winner.station_id)
        route_url = (
            f"{self.base_url}/route/v1/driving/"
            f"{coords[winner_index]};{coords[-1]}?overview=full&geometries=geojson"
        )
        try:
            geometry, distance, duration = parse_route_response(self._get_json(route_url))
        except RoutingError as exc:
            return RouteComparison(
                routes=routes,
                fastest_station_id=winner.station_id,
                geometry=None,
                message=f"Route comparison succeeded, but the selected route line is unavailable: {exc}",
            )
        updated_routes = tuple(
            StationRoute(
                station_id=route.station_id,
                station_name=route.station_name,
                distance_meters=distance if route.station_id == winner.station_id else route.distance_meters,
                duration_seconds=duration if route.station_id == winner.station_id else route.duration_seconds,
                success=route.success,
                reason=route.reason,
            )
            for route in routes
        )
        return RouteComparison(
            routes=updated_routes,
            fastest_station_id=winner.station_id,
            geometry=geometry,
            message=(
                "Fastest returned route among the queried stations; "
                "OSRM duration is not live traffic or an emergency-response guarantee."
            ),
        )
