from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from config import GEOGRAPHIC_CRS


@dataclass(frozen=True)
class Station:
    station_id: str
    name: str
    category: str
    source: str
    verification_status: str
    geometry: Point


class StationManager:
    """Keep mapped OSM stations and temporary proposed stations distinguishable."""

    def __init__(self, real_stations: gpd.GeoDataFrame) -> None:
        if real_stations.crs is None:
            raise ValueError("Station data must declare a CRS.")
        if not real_stations.empty and not real_stations.geometry.geom_type.isin(["Point"]).all():
            raise ValueError("Station data must contain point geometries.")
        self._real_stations = real_stations.to_crs(GEOGRAPHIC_CRS).copy()
        self._proposed: list[Station] = []
        self._next_proposal_number = 1

    @property
    def real_count(self) -> int:
        return len(self._real_stations)

    @property
    def proposed_count(self) -> int:
        return len(self._proposed)

    def all_stations(self) -> gpd.GeoDataFrame:
        proposed = gpd.GeoDataFrame(
            [
                {
                    "station_id": station.station_id,
                    "name": station.name,
                    "category": station.category,
                    "source": station.source,
                    "verification_status": station.verification_status,
                    "geometry": station.geometry,
                }
                for station in self._proposed
            ],
            columns=[
                "station_id",
                "name",
                "category",
                "source",
                "verification_status",
                "geometry",
            ],
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
        if self._real_stations.empty:
            return proposed
        if proposed.empty:
            return self._real_stations.copy()
        return gpd.GeoDataFrame(
            pd.concat([self._real_stations, proposed], ignore_index=True),
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )

    def add_proposed(self, location: Point) -> Station:
        if not isinstance(location, Point) or location.is_empty:
            raise ValueError("A proposed station location must be a non-empty point.")
        if not (-180 <= location.x <= 180 and -90 <= location.y <= 90):
            raise ValueError("Proposed station coordinates must be longitude/latitude.")
        station = Station(
            station_id=f"proposed-{self._next_proposal_number:03d}",
            name=f"Proposed station {self._next_proposal_number}",
            category="PROPOSED",
            source="User-created simulation",
            verification_status="Hypothetical location; not an existing facility",
            geometry=location,
        )
        self._next_proposal_number += 1
        self._proposed.append(station)
        return station

    def remove_proposed(self, station_id: str) -> bool:
        before = len(self._proposed)
        self._proposed = [
            station for station in self._proposed if station.station_id != station_id
        ]
        return len(self._proposed) != before

    def reset_proposed(self) -> None:
        self._proposed.clear()
        self._next_proposal_number = 1

    def proposed_stations(self) -> tuple[Station, ...]:
        return tuple(self._proposed)
