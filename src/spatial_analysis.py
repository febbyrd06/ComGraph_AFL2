from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd

from config import BUFFER_DISTANCES_METERS, PROJECTED_CRS


@dataclass(frozen=True)
class CoverageMetric:
    radius_meters: int
    total_samples: int
    covered_samples: int
    uncovered_samples: int
    percentage: float
    covered_sample_ids: frozenset[str]


def calculate_coverage(
    samples: gpd.GeoDataFrame,
    stations: gpd.GeoDataFrame,
    radii: tuple[int, ...] = BUFFER_DISTANCES_METERS,
) -> dict[int, CoverageMetric]:
    """Measure straight-line point coverage using buffers in the projected CRS."""
    if samples.empty:
        raise ValueError("Coverage analysis requires at least one residential sample point.")
    if samples.crs is None:
        raise ValueError("Residential sample points must declare a CRS.")
    if "sample_id" not in samples.columns:
        raise ValueError("Residential sample points must have a sample_id column.")
    if stations.crs is None:
        raise ValueError("Fire stations must declare a CRS.")
    if stations.geometry.isna().any() or stations.geometry.is_empty.any():
        raise ValueError("Fire-station geometries must not be empty.")
    if not samples.geometry.geom_type.isin(["Point"]).all():
        raise ValueError("Residential samples must be point geometries.")
    if not stations.empty and not stations.geometry.geom_type.isin(["Point"]).all():
        raise ValueError("Fire stations must be point geometries.")

    projected_samples = samples.to_crs(PROJECTED_CRS)
    projected_stations = stations.to_crs(PROJECTED_CRS)
    results: dict[int, CoverageMetric] = {}
    for radius in radii:
        if radius <= 0:
            raise ValueError("Buffer radii must be greater than zero.")
        if projected_stations.empty:
            covered_ids: set[str] = set()
        else:
            buffers = gpd.GeoDataFrame(
                geometry=projected_stations.geometry.buffer(radius),
                crs=PROJECTED_CRS,
            )
            samples_with_ids = projected_samples[["sample_id", "geometry"]]
            joined = gpd.sjoin(
                samples_with_ids,
                buffers,
                how="inner",
                predicate="intersects",
            )
            covered_ids = set(joined["sample_id"].astype(str))
        total = len(projected_samples)
        covered = len(covered_ids)
        results[radius] = CoverageMetric(
            radius_meters=radius,
            total_samples=total,
            covered_samples=covered,
            uncovered_samples=total - covered,
            percentage=(covered / total * 100.0) if total else 0.0,
            covered_sample_ids=frozenset(covered_ids),
        )
    return results


def station_buffer_geometries(
    stations: gpd.GeoDataFrame,
    radius_meters: int,
) -> gpd.GeoSeries:
    """Return station buffers measured in EPSG:32749 meters."""
    if radius_meters <= 0:
        raise ValueError("Buffer radius must be greater than zero.")
    if stations.crs is None:
        raise ValueError("Fire stations must declare a CRS.")
    if stations.empty:
        return gpd.GeoSeries([], crs=PROJECTED_CRS)
    return stations.to_crs(PROJECTED_CRS).geometry.buffer(radius_meters)


def geographic_sample_copy(samples: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return samples in longitude/latitude for map display and exports."""
    if samples.crs is None:
        raise ValueError("Residential sample points must declare a CRS.")
    return samples.to_crs(GEOGRAPHIC_CRS)
