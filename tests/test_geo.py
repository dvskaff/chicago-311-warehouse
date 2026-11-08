"""Community-area map: matching boundaries to areas, geometry clean-up, colors and the GeoJSON written for GitHub."""

import json
import math

import pandas as pd

from c311 import fixture, geo

AREAS = pd.DataFrame(
    {"community_area_id": [1, 2, 3], "community_area_name": ["ROGERS PARK", "WEST RIDGE", "UPTOWN"]}
)


def _square(x0=-87.7, y0=41.9, d=0.01):
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x0 + d, y0], [x0 + d, y0 + d], [x0, y0 + d], [x0, y0]]],
    }


def test_parse_matches_by_number_or_name_and_skips_bad_features():
    gj = {"features": [
        {"properties": {"AREA_NUMBE": "1", "COMMUNITY": "ROGERS PARK"}, "geometry": _square()},
        {"properties": {"area_num_1": 2.0}, "geometry": _square()},
        {"properties": {"community": "Uptown"}, "geometry": _square()},  # no number: matched by name
        {"properties": {"area_numbe": "1"}, "geometry": _square()},  # duplicate
        {"properties": {"area_numbe": "9"}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"properties": {"community": "Nowhere"}, "geometry": _square()},
    ]}  # fmt: skip
    b = geo.parse(gj, AREAS)
    assert sorted(b["community_area_id"]) == [1, 2, 3]
    assert json.loads(b["geometry"].iloc[0])["type"] == "Polygon"
    assert len(geo.parse(fixture.boundaries(), AREAS)) == 77


def test_simplify_keeps_shape_and_closed_rings():
    n = 400  # a finely sampled circle
    ring = [[-87.7 + 0.01 * math.cos(2 * math.pi * k / n), 41.9 + 0.01 * math.sin(2 * math.pi * k / n)]
            for k in range(n)] + [[-87.69, 41.9]]  # fmt: skip
    out = geo.simplify({"type": "Polygon", "coordinates": [ring]})["coordinates"][0]
    assert 8 < len(out) < len(ring) / 3
    assert out[0] == out[-1]
    assert abs(abs(geo._area(out)) - abs(geo._area(ring))) / abs(geo._area(ring)) < 0.02
    tri = [[0, 0], [1, 0], [0, 1], [0, 0]]  # too small to simplify: left alone
    assert geo.simplify({"type": "MultiPolygon", "coordinates": [[tri]]})["coordinates"] == [[tri]]


def test_rewind_for_d3_makes_outer_rings_clockwise_and_holes_counterclockwise():
    outer = _square(d=1.0)["coordinates"][0]  # counter-clockwise (GeoJSON standard)
    hole = [[-87.5, 42.2], [-87.5, 42.4], [-87.3, 42.4], [-87.3, 42.2], [-87.5, 42.2]]  # clockwise
    assert geo._area(outer) > 0 and geo._area(hole) < 0
    r = geo.rewind_for_d3({"type": "Polygon", "coordinates": [outer, hole]})["coordinates"]
    assert geo._area(r[0]) < 0 and geo._area(r[1]) > 0
    m = geo.rewind_for_d3({"type": "MultiPolygon", "coordinates": [[outer]]})["coordinates"]
    assert geo._area(m[0][0]) < 0


def test_colors_diverge_around_one():
    assert geo.color(1.0) == "#f0efea"  # neutral = typical speed
    assert geo.color(0.6) == "#2a78d6" and geo.color(0.1) == "#2a78d6"  # faster, clamped
    assert geo.color(1.4) == "#eb6834" and geo.color(9.0) == "#eb6834"  # slower, clamped
    assert geo.color(None) == geo.color(float("nan")) == "#d9d8d2"  # no index


def test_feature_collection_and_choropleth():
    boundaries = pd.DataFrame(
        {"community_area_id": [1, 2], "geometry": [json.dumps(_square()), json.dumps(_square())]}
    )
    areas = AREAS.assign(service_index=[0.8, None, 1.1], median_days_to_close=[2.0, None, 3.5],
                         pct_closed_within_7d=[0.9, None, 0.7], closed_requests=[1200, 0, 900])  # fmt: skip
    fc = geo.feature_collection(boundaries, areas, "2026-11-01")
    p1, p2 = (f["properties"] for f in fc["features"])
    assert (
        p1["community_area"] == "ROGERS PARK"
        and p1["service_index"] == 0.8
        and p1["closed_within_7_days"] == "90%"
    )
    assert p1["fill"] == geo.color(0.8) and p1["data_through"] == "2026-11-01"
    assert p2["service_index"] is None and p2["fill"] == "#d9d8d2" and p2["closed_requests"] == 0
    fig = geo.fig_map(fc)
    assert list(fig.data[0].locations) == [1]  # areas without an index are drawn grey, not colored
    assert geo._area(fig.data[0].geojson["features"][0]["geometry"]["coordinates"][0]) < 0  # rewound for d3
    assert (
        fc["features"][0]["geometry"]["coordinates"][0] == _square()["coordinates"][0]
    )  # the file keeps RFC order
