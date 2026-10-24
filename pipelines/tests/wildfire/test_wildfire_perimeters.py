"""fire-perimeters: current GeoJSON and the archive carried from version to version."""

import json
from datetime import timedelta

import pytest
from wildfire_helpers import NOW, PERIMETERS_COUNT, FakeGet, count_url, fixture

from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.layout import blob_key, manifest_key, version_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_wildfire import _common, wfigs_perimeters as wp

COOLEY = "730A7DAE-E447-4B05-BFF0-CCC274788DE5"
BRAS = "209FB86B-237A-4FF3-9BFA-3A1FC8D99647"
DAY_MS = 86_400_000


def page():
    return json.loads(fixture("perimeters-page-000.json"))


def body(doc):
    return json.dumps(doc, separators=(",", ":")).encode()


def test_build_from_one_page():
    files, stats = wp.build({"page-000": fixture("perimeters-page-000.json")})
    assert stats["rows"] == 4 and stats["skipped_without_geometry_or_irwin"] == 0
    current = json.loads(files["current.geojson"])
    assert current["type"] == "FeatureCollection" and len(current["features"]) == 4
    cooley = next(f for f in current["features"] if f["properties"]["irwin_id"] == COOLEY)
    assert cooley["properties"]["incident_name"] == "COOLEY"
    assert cooley["properties"]["gis_acres"] == 1370.23
    assert cooley["properties"]["date_current"] == "2026-10-20T00:04:55Z"  # 1792454695000 ms
    assert cooley["properties"]["state"] == "us-ca"
    path = cooley["properties"]["perimeter_path"]
    assert path.startswith(f"perimeters/{COOLEY}/20261020T000455Z-") and path in files
    archived = json.loads(files[path])
    assert archived["geometry"] == cooley["geometry"] and archived["type"] == "Feature"
    # The snapshot is stamped with the newest edit in the data (COOLEY's incident record, 2026-10-23 23:56:41Z).
    snaps = json.loads(files["snapshots/index.json"])["snapshots"]
    assert len(snaps) == 1 and snaps[0]["snapshot_at"] == "2026-10-23T23:56:41Z" and snaps[0]["perimeters"] == 4
    assert snaps[0]["path"].startswith("snapshots/2026-10-23/235641Z-")
    members = json.loads(files[snaps[0]["path"]])["perimeters"]
    assert {m["path"] for m in members} == {f["properties"]["perimeter_path"] for f in current["features"]}


def test_coordinates_match_the_source_to_5_decimals():
    files, _ = wp.build({"page-000": fixture("perimeters-page-000.json")})
    src = next(f for f in page()["features"] if f["properties"]["attr_IncidentName"] == "Bras")
    out = next(f for f in json.loads(files["current.geojson"])["features"] if f["properties"]["irwin_id"] == BRAS)
    a, b = src["geometry"]["coordinates"][0][:5], out["geometry"]["coordinates"][0][:5]
    assert [[round(x, 5), round(y, 5)] for x, y in a] == b


def test_features_without_geometry_are_skipped():
    doc = page()
    doc["features"][0]["geometry"] = None
    _, stats = wp.build({"page-000": body(doc)})
    assert stats["rows"] == 3 and stats["skipped_without_geometry_or_irwin"] == 1


def test_empty_layer_publishes_empty_files():
    doc = page()
    doc["features"] = []
    files, stats = wp.build({"page-000": body(doc)})
    assert stats["rows"] == 0 and json.loads(files["current.geojson"])["features"] == []
    assert json.loads(files["snapshots/index.json"])["snapshots"] == []


class Layer:
    """The perimeters service as collect() sees it: the count request and one page."""

    def __init__(self, monkeypatch):
        self.doc = page()
        self.monkeypatch = monkeypatch

    def install(self):
        count = {count_url(PERIMETERS_COUNT): fixture("perimeters-count.json")}
        self.monkeypatch.setattr(CollectContext, "get", FakeGet(count))
        ctx = CollectContext(load_contract("wildfiredata/fire-perimeters"), NOW)
        urls = _common.page_urls(ctx, wp.PERIMETERS_LAYER, wp.QUERY, wp.PAGE_SIZE)
        self.monkeypatch.setattr(CollectContext, "get", FakeGet({**count, **{u: body(self.doc) for u in urls.values()}}))


def run(store, layer, now):
    layer.install()
    return run_dataset(load_contract("wildfiredata/fire-perimeters"), store, now=now)


def published(store, result):
    doc = get_json(store, version_key("wildfiredata", "fire-perimeters", result.version))[0]
    read = lambda path: json.loads(store.get(blob_key(doc["files"][path]["blob"]))[0])  # noqa: E731
    return doc, read


def test_archive_survives_fall_off_and_ages_out(store, monkeypatch):
    layer = Layer(monkeypatch)
    first = run(store, layer, NOW)
    assert first.outcome == "published"
    assert run(store, layer, NOW).outcome == "unchanged"  # the previous archive does not count as a change

    # COOLEY falls off the current layer and Bras is remapped (its last vertex before the closing one dropped).
    cooley = next(f for f in layer.doc["features"] if f["properties"]["poly_IRWINID"].strip("{}") == COOLEY)
    layer.doc["features"].remove(cooley)
    bras = next(f for f in layer.doc["features"] if f["properties"]["poly_IRWINID"].strip("{}") == BRAS)
    ring = bras["geometry"]["coordinates"][0]
    del ring[-2]
    bras["properties"]["poly_DateCurrent"] += DAY_MS
    bras["properties"]["attr_ModifiedOnDateTime_dt"] = 1792799801263 + DAY_MS
    second = run(store, layer, NOW + timedelta(hours=3))
    assert second.outcome == "published"
    doc, read = published(store, second)
    shapes = read("perimeters/index.json")["perimeters"]
    assert sum(1 for s in shapes if s["irwin_id"] == BRAS) == 2  # both Bras shapes
    old_cooley = next(s for s in shapes if s["irwin_id"] == COOLEY)
    assert old_cooley["retained"] and old_cooley["path"] in doc["files"] and old_cooley["blob"] is None
    snaps = read("snapshots/index.json")["snapshots"]
    assert [s["perimeters"] for s in snaps] == [4, 3]
    assert all(s["retained"] and s["blob"] is None for s in snaps)
    assert len(read("current.geojson")["features"]) == 3

    # Fifteen days on, an edit to the remaining fires: the first snapshot and COOLEY's shape leave the
    # current version but stay listed with the hash of the blob that holds them.
    for f in layer.doc["features"]:
        f["properties"]["attr_ModifiedOnDateTime_dt"] = 1792799801263 + 15 * DAY_MS
    third = run(store, layer, NOW + timedelta(days=15))
    doc3, read3 = published(store, third)
    snaps3 = read3("snapshots/index.json")["snapshots"]
    assert [s["retained"] for s in snaps3] == [False, True, True]
    assert snaps3[0]["path"] not in doc3["files"]
    gone = next(s for s in read3("perimeters/index.json")["perimeters"] if s["irwin_id"] == COOLEY)
    doc2, _ = published(store, second)
    assert not gone["retained"] and gone["path"] not in doc3["files"] and gone["blob"] == doc2["files"][gone["path"]]["blob"]
    assert snaps3[0]["blob"] == doc2["files"][snaps3[0]["path"]]["blob"]
    # ...and the earlier version still serves it.
    assert json.loads(store.get(blob_key(gone["blob"]))[0])["properties"]["incident_name"] == "COOLEY"
    assert get_json(store, manifest_key("wildfiredata", "fire-perimeters"))[0]["current"] == third.version


def test_collect_outside_an_engine_run_has_no_history(monkeypatch):
    layer = Layer(monkeypatch)
    layer.install()
    raw = wp.collect(CollectContext(load_contract("wildfiredata/fire-perimeters"), NOW))
    assert sorted(raw) == ["page-000"]


def test_previous_listing_does_not_affect_change_detection():
    assert wp.normalize("previous.json", b"anything") == b""
    assert wp.normalize("page-000", b"x") == b"x"


def test_a_listed_archive_needs_the_engine():
    raw = {"page-000": fixture("perimeters-page-000.json"), "previous.json": b'{"files":{"snapshots/index.json":"00"}}'}
    with pytest.raises(RuntimeError, match="inside a datasites run"):
        wp.build(raw)
