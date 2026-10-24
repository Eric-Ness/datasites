"""boundaries: ids, geometry rules and index.json, built from real subsets of the source files."""

import json
import re

import pytest
from visualize_helpers import by_id, features, fixture_raw, polygons, rings

from datasites_core import geo
from datasites_visualize import boundaries
from datasites_visualize.geometry import signed_area

GEOJSON_PATHS = [layer.path for layer in boundaries.LAYERS]


@pytest.fixture
def built(monkeypatch):
    monkeypatch.setattr(boundaries, "REQUIRE_FULL_COVERAGE", False)  # the fixtures are subsets
    return boundaries.build(fixture_raw())


def test_every_layer_and_the_index_are_built(built):
    files, stats = built
    assert sorted(files) == sorted([*GEOJSON_PATHS, "index.json"])
    counts = {p: len(features(files[p])) for p in GEOJSON_PATHS}
    assert stats["rows"] == sum(counts.values()) == 5 + 1 + 2 + 2 + 2 + 2 + 6 + 3
    index = json.loads(files["index.json"])
    assert [e["path"] for e in index["layers"]] == GEOJSON_PATHS
    for entry in index["layers"]:
        assert entry["feature_count"] == counts[entry["path"]]
        assert entry["bytes"] == len(files[entry["path"]])
        assert entry["license"] and entry["vintage"] and entry["caveats"] and entry["source"]["url"]
        assert entry["commercial_ok"] is True
    assert index["feature_count"] == stats["rows"]
    assert "NAD83" in index["coordinates"]


def test_county_ids_match_the_registry(built):
    counties = by_id(built[0]["us/counties-20m.geojson"])
    assert sorted(counties) == [
        "us-county-02016", "us-county-06037", "us-county-09110", "us-county-51003", "us-county-51540",
    ]
    la = counties["us-county-06037"]
    assert la["properties"] == {"id": "us-county-06037", "name": "Los Angeles County", "fips": "06037", "state": "us-ca"}
    assert la["geometry"]["type"] == "MultiPolygon"  # the mainland plus Santa Catalina and San Clemente
    xs = [x for _, ring in rings(la) for x, _ in ring]
    ys = [y for _, ring in rings(la) for _, y in ring]
    assert -118.95 < min(xs) < -118.6 and -117.8 < max(xs) < -117.6
    assert 32.7 < min(ys) < 33.0 and 34.8 < max(ys) < 34.9
    registry = geo.load()
    assert all(fid in registry for fid in counties)
    assert counties["us-county-09110"]["properties"]["name"] == registry["us-county-09110"]["name"]  # CT planning region


def test_island_area_counties_are_listed_not_invented(built):
    files = built[0]
    assert list(by_id(files["us/counties-5m.geojson"])) == ["us-county-06037"]
    entry = next(e for e in json.loads(files["index.json"])["layers"] if e["path"] == "us/counties-5m.geojson")
    assert [e["geoid"] for e in entry["excluded"]] == ["60010"]
    assert entry["excluded"][0]["state"] == "us-as"


def test_unknown_county_fails_instead_of_inventing_an_id(monkeypatch):
    registry = dict(geo.load())
    del registry["us-county-06037"]
    monkeypatch.setattr(boundaries.geo, "load", lambda: registry)
    with pytest.raises(ValueError, match="no registry id.*06037"):
        boundaries.build_layer(boundaries.LAYERS[1], fixture_raw()["county-20m"])


def test_full_coverage_is_required_on_real_runs():
    with pytest.raises(ValueError, match="registry states with no boundary"):
        boundaries.build(fixture_raw())


def test_state_ids(built):
    files = built[0]
    assert sorted(by_id(files["us/states-20m.geojson"])) == ["us-dc", "us-ri"]
    states_5m = by_id(files["us/states-5m.geojson"])
    assert states_5m["us-gu"]["properties"] == {"id": "us-gu", "name": "Guam", "usps": "GU", "fips": "66", "kind": "territory"}


def test_district_ids(built):
    files = built[0]
    cds = by_id(files["us/congressional-districts-20m.geojson"])
    assert sorted(cds) == ["us-cd-1000", "us-cd-1198"]
    assert cds["us-cd-1000"]["properties"] | {} == {
        "id": "us-cd-1000", "name": "Delaware Congressional District (at Large)", "state": "us-de", "fips": "1000",
        "district": "00", "at_large": True, "voting": True, "congress": 119,
    }
    assert cds["us-cd-1198"]["properties"]["voting"] is False and cds["us-cd-1198"]["properties"]["state"] == "us-dc"
    assert "us-cd-6698" in by_id(files["us/congressional-districts-5m.geojson"])


def test_country_ids_and_the_minus_99_rule(built):
    files = built[0]
    world = by_id(files["world/countries-110m.geojson"])
    # Norway and France have ISO_A3 -99 in Natural Earth; their ADM0_A3 is the ISO code.
    assert sorted(world) == ["fji", "fra", "lso", "ne-kos", "nor", "zaf"]
    assert world["nor"]["properties"]["registry"] is True and world["nor"]["properties"]["name"] == "Norway"
    kos = world["ne-kos"]["properties"]
    assert kos["registry"] is False and kos["iso_a3"] is None and kos["adm0_a3"] == "KOS"
    # Indian Ocean Ter. has ISO_A3_EH AUS; without the -99 rule it would collide with Australia.
    world50 = by_id(files["world/countries-50m.geojson"])
    assert sorted(world50) == ["aus", "ne-ioa", "ne-kos"]
    index = json.loads(files["index.json"])
    entry = next(e for e in index["layers"] if e["path"] == "world/countries-50m.geojson")
    assert [u["id"] for u in entry["not_in_registry"]] == ["ne-ioa", "ne-kos"]
    assert "cxr" in entry["not_in_registry"][0]["note"]


def test_rings_are_closed_oriented_and_rounded(built):
    files = built[0]
    many_decimals = re.compile(rb"\.\d{6,}")
    for path in GEOJSON_PATHS:
        assert not many_decimals.search(files[path]), path
        for feature in features(files[path]):
            assert feature["id"] == feature["properties"]["id"]
            for exterior, ring in rings(feature):
                assert len(ring) >= 4 and ring[0] == ring[-1], feature["id"]
                assert (signed_area(ring) > 0) == exterior, (path, feature["id"])
                for (x1, _), (x2, _) in zip(ring, ring[1:]):
                    assert abs(x2 - x1) <= 180, feature["id"]


def test_holes_and_antimeridian_parts_survive(built):
    files = built[0]
    counties = by_id(files["us/counties-20m.geojson"])
    albemarle = polygons(counties["us-county-51003"])
    assert len(albemarle) == 1 and len(albemarle[0]) == 2  # Charlottesville is a hole in Albemarle
    city = polygons(counties["us-county-51540"])[0][0]
    hole = albemarle[0][1]
    assert sorted(map(tuple, hole[:-1])) == sorted(map(tuple, city[:-1]))
    aleutians = polygons(counties["us-county-02016"])
    assert any(min(x for x, _ in p[0]) > 0 for p in aleutians) and any(max(x for x, _ in p[0]) < 0 for p in aleutians)
    fiji = polygons(by_id(files["world/countries-110m.geojson"])["fji"])
    assert any(x == 180 for p in fiji for x, _ in p[0]) and any(x == -180 for p in fiji for x, _ in p[0])
    zaf = polygons(by_id(files["world/countries-110m.geojson"])["zaf"])
    assert any(len(p) == 2 for p in zaf)  # Lesotho's hole


def test_build_is_deterministic(monkeypatch):
    monkeypatch.setattr(boundaries, "REQUIRE_FULL_COVERAGE", False)
    assert boundaries.build(fixture_raw()) == boundaries.build(fixture_raw())
