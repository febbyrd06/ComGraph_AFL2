import geopandas as gpd
import pytest
from shapely.geometry import Point

from config import GEOGRAPHIC_CRS, PROJECTED_CRS
from src.spatial_analysis import calculate_coverage, station_buffer_geometries


def _point_layer(points, ids, crs):
    return gpd.GeoDataFrame(
        {"sample_id": ids},
        geometry=points,
        crs=crs,
    )


def test_surabaya_projection_and_three_kilometer_buffer_units():
    location = gpd.GeoSeries([Point(112.7, -7.25)], crs=GEOGRAPHIC_CRS).to_crs(PROJECTED_CRS)
    point = location.iloc[0]
    area = PROJECTED_CRS.area_of_use
    assert 108 <= 112.7 <= 114
    assert area.west <= 112.7 <= area.east
    assert area.south <= -7.25 <= area.north

    stations = _point_layer([point], ["station-1"], PROJECTED_CRS)
    buffer = station_buffer_geometries(stations, 3_000).iloc[0]
    assert buffer.bounds[0] == pytest.approx(point.x - 3_000, abs=1)
    assert buffer.bounds[2] == pytest.approx(point.x + 3_000, abs=1)
    assert buffer.area > 0


def test_coverage_counts_unique_samples_for_three_and_five_kilometers():
    station = gpd.GeoSeries([Point(112.7, -7.25)], crs=GEOGRAPHIC_CRS).to_crs(PROJECTED_CRS).iloc[0]
    projected_samples = _point_layer(
        [
            Point(station.x + 1_000, station.y),
            Point(station.x + 4_000, station.y),
            Point(station.x + 7_000, station.y),
        ],
        ["near", "middle", "far"],
        PROJECTED_CRS,
    ).to_crs(GEOGRAPHIC_CRS)
    stations = _point_layer([station], ["station-1"], PROJECTED_CRS).to_crs(GEOGRAPHIC_CRS)
    results = calculate_coverage(projected_samples, stations)
    assert results[3_000].covered_samples == 1
    assert results[3_000].uncovered_samples == 2
    assert results[3_000].percentage == pytest.approx(100 / 3)
    assert results[5_000].covered_samples == 2
    assert results[5_000].uncovered_samples == 1


def test_coverage_requires_nonempty_samples_and_crs():
    empty = gpd.GeoDataFrame(
        {"sample_id": []},
        geometry=gpd.GeoSeries([], crs=GEOGRAPHIC_CRS),
        crs=GEOGRAPHIC_CRS,
    )
    stations = _point_layer([Point(112.7, -7.25)], ["station-1"], GEOGRAPHIC_CRS)
    with pytest.raises(ValueError, match="at least one"):
        calculate_coverage(empty, stations)

    missing_crs = _point_layer([Point(112.7, -7.25)], ["sample-1"], None)
    with pytest.raises(ValueError, match="declare a CRS"):
        calculate_coverage(missing_crs, stations)
