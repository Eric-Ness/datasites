"""Ring handling: rounding, closure, collapse, RFC 7946 orientation, hole assignment, the antimeridian."""

import pytest

from datasites_visualize.geometry import (
    GeometryError,
    clean_ring,
    geometry_json,
    polygons_from_geojson,
    polygons_from_shapefile_rings,
    rnd,
    signed_area,
)

# A 2x2 square clockwise (the shapefile convention for an exterior) and a 1x1 hole counterclockwise.
SQUARE_CW = [(0, 0), (0, 2), (2, 2), (2, 0), (0, 0)]
HOLE_CCW = [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5), (0.5, 0.5)]


def test_rounding_to_five_decimals_and_no_negative_zero():
    assert rnd(-118.123456789) == -118.12346
    assert rnd(34.000004) == 34.0
    assert rnd(-0.000004) == 0.0 and str(rnd(-0.000004)) == "0.0"
    ring = clean_ring([(0.1234567, 0), (0, 1.0000049), (1, 1), (0.1234567, 0)])
    assert ring == [(0.12346, 0.0), (0.0, 1.0), (1.0, 1.0), (0.12346, 0.0)]


def test_rings_are_closed_and_repeats_removed():
    open_ring = [(0, 0), (0, 1), (0, 1.000001), (1, 1), (1, 0)]
    ring = clean_ring(open_ring)
    assert ring[0] == ring[-1]
    assert ring == [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]


def test_collapsed_rings_are_dropped():
    assert clean_ring([(0, 0), (0.000001, 0), (0, 0.000001), (0, 0)]) is None  # all one point after rounding
    assert clean_ring([(0, 0), (1, 1), (2, 2), (0, 0)]) is None  # zero area


def test_shapefile_rings_become_rfc7946_oriented_polygons():
    polys = polygons_from_shapefile_rings([SQUARE_CW, HOLE_CCW], "t")
    assert len(polys) == 1 and len(polys[0]) == 2
    exterior, hole = polys[0]
    assert signed_area(exterior) > 0  # counterclockwise
    assert signed_area(hole) < 0  # clockwise


def test_holes_go_to_the_exterior_that_contains_them():
    far_cw = [(10, 10), (10, 12), (12, 12), (12, 10), (10, 10)]
    far_hole = [(10.5, 10.5), (11.5, 10.5), (11.5, 11.5), (10.5, 11.5), (10.5, 10.5)]
    polys = polygons_from_shapefile_rings([SQUARE_CW, far_cw, far_hole, HOLE_CCW], "t")
    assert [len(p) for p in polys] == [2, 2]
    assert set(polys[0][1]) == set(HOLE_CCW) and set(polys[1][1]) == set(far_hole)


def test_an_island_in_a_lake_keeps_its_own_hole():
    big = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]  # cw exterior
    lake = [(1, 1), (9, 1), (9, 9), (1, 9), (1, 1)]  # ccw hole of big
    island = [(3, 3), (3, 7), (7, 7), (7, 3), (3, 3)]  # cw exterior inside the lake
    pond = [(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)]  # ccw hole of the island, also inside big's outline
    polys = polygons_from_shapefile_rings([big, lake, island, pond], "t")
    assert len(polys) == 2
    assert polys[0][1][0] in lake and polys[1][1][0] in pond


def test_an_uncontained_hole_is_kept_as_an_exterior():
    stray = [(20, 20), (21, 20), (21, 21), (20, 21), (20, 20)]  # ccw, outside everything
    polys = polygons_from_shapefile_rings([SQUARE_CW, stray], "t")
    assert len(polys) == 2 and all(signed_area(p[0]) > 0 for p in polys)


def test_geojson_input_is_reoriented():
    # Natural Earth's files run exteriors clockwise; the output must be counterclockwise.
    polys = polygons_from_geojson({"type": "Polygon", "coordinates": [SQUARE_CW, HOLE_CCW]}, "t")
    assert signed_area(polys[0][0]) > 0 and signed_area(polys[0][1]) < 0


def test_antimeridian_jumps_are_rejected_but_pole_edges_allowed():
    across = [(179.5, 0), (-179.5, 0), (-179.5, 1), (179.5, 1), (179.5, 0)]
    with pytest.raises(GeometryError, match="antimeridian"):
        polygons_from_geojson({"type": "Polygon", "coordinates": [across]}, "t")
    antarctica = [(-180, -85), (0, -80), (180, -85), (180, -90), (-180, -90), (-180, -85)]
    assert polygons_from_geojson({"type": "Polygon", "coordinates": [antarctica]}, "t")


def test_geometry_json_is_compact_and_typed():
    one = polygons_from_shapefile_rings([SQUARE_CW], "t")
    assert geometry_json(one) == '{"type":"Polygon","coordinates":[[[0,0],[2,0],[2,2],[0,2],[0,0]]]}'
    two = polygons_from_shapefile_rings([SQUARE_CW, [(5, 5), (5, 6), (6, 6), (6, 5), (5, 5)]], "t")
    assert geometry_json(two).startswith('{"type":"MultiPolygon","coordinates":[[[[0,0]')
