import geopandas as gpd
from shapely.geometry import Point

from config import GEOGRAPHIC_CRS, PROJECTED_CRS
from src.spatial_analysis import calculate_coverage


def test_original_synthetic_example_has_four_stations_and_thirteen_samples():
    origin = gpd.GeoSeries([Point(112.7, -7.25)], crs=GEOGRAPHIC_CRS).to_crs(PROJECTED_CRS).iloc[0]
    station_offsets = [
        (-10_000, -10_000),
        (10_000, -10_000),
        (-10_000, 10_000),
        (10_000, 10_000),
    ]
    stations_projected = gpd.GeoDataFrame(
        {
            "station_id": [f"synthetic-{index}" for index in range(1, 5)],
            "name": [f"Synthetic station {index}" for index in range(1, 5)],
            "category": ["SYNTHETIC"] * 4,
        },
        geometry=[
            Point(origin.x + dx, origin.y + dy)
            for dx, dy in station_offsets
        ],
        crs=PROJECTED_CRS,
    )
    sample_points = []
    for dx, dy in station_offsets:
        sample_points.extend(
            [
                Point(origin.x + dx, origin.y + dy),
                Point(origin.x + dx + 2_500, origin.y + dy),
                Point(origin.x + dx, origin.y + dy + 4_500),
            ]
        )
    sample_points.append(Point(origin.x + 30_000, origin.y + 30_000))
    samples = gpd.GeoDataFrame(
        {
            "sample_id": [f"sample-{index:02d}" for index in range(1, 14)],
            "data_type": ["SYNTHETIC SAMPLE"] * 13,
        },
        geometry=sample_points,
        crs=PROJECTED_CRS,
    )

    assert len(stations_projected) == 4
    assert len(samples) == 13
    results = calculate_coverage(
        samples.to_crs(GEOGRAPHIC_CRS),
        stations_projected.to_crs(GEOGRAPHIC_CRS),
    )
    assert results[3_000].covered_samples == 8
    assert results[5_000].covered_samples == 12
