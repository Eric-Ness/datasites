"""Engine features the domain pipelines rely on: table/files kinds, registry and pattern locations,
sharded version documents, collect() pipelines and the raw-archive size cap."""

import json
import sys
import types
from datetime import datetime, timezone

import polars as pl
import pytest

from datasites_core import publish
from datasites_core.contracts import ContractError, parse_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import blob_key, manifest_key, version_key
from datasites_core.store import get_json

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
SOURCE = {
    "id": "test-source",
    "name": "Test source",
    "url": "https://example.org/",
    "license": "CC0-1.0",
    "commercial_ok": True,
    "attribution": "Test",
}


def contract(**overrides):
    data = {
        "id": "thing",
        "version": 1,
        "title": "Thing",
        "description": "A test dataset",
        "check_every": "P1D",
        "cadence": "P1D",
        "sla": "P7D",
        "source": SOURCE,
        **overrides,
    }
    return parse_contract("testsite", data, "test")


def install(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def static_fetcher(bodies):
    calls = []

    def fetcher(url, etag, last_modified):
        calls.append((url, etag))
        if etag == f'"{len(bodies[url])}"':
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, bodies[url], f'"{len(bodies[url])}"', "Sat, 24 Oct 2026 10:00:00 GMT")

    fetcher.calls = calls
    return fetcher


def doc_of(store, c, vid):
    return get_json(store, version_key(c.site, c.id, vid))[0]


# --- table kind --------------------------------------------------------------------------------

TABLE_SCHEMA = [
    {"name": "id", "type": "str"},
    {"name": "group", "type": "str"},
    {"name": "factor", "type": "float", "unit": "kg CO2e/kWh"},
]


def test_table_dataset_publishes_partitions(store):
    rows = pl.DataFrame({"id": ["b", "a", "c"], "group": ["x", "x", "y"], "factor": [0.2, 0.1, 0.3]})
    install("fake_table", RESOURCES={"t": "https://example.org/t.csv"}, transform=lambda raw: rows)
    c = contract(kind="table", pipeline="fake_table", schema=TABLE_SCHEMA, key=["id"], partition="group")
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/t.csv": b"x"}), now=NOW)
    assert result.outcome == "published"
    doc = doc_of(store, c, result.version)
    assert doc["kind"] == "table" and doc["key"] == ["id"] and doc["partition"] == "group"
    assert sorted(doc["files"]) == [
        "download/thing.csv", "download/thing.parquet",
        "table/all.csv", "table/all.json", "table/x.csv", "table/x.json", "table/y.csv", "table/y.json",
    ]
    x = json.loads(store.get(blob_key(doc["files"]["table/x.json"]["blob"]))[0])
    assert [r["id"] for r in x["rows"]] == ["a", "b"]  # sorted by key
    assert x["meta"]["partition"] == {"group": "x"} and x["meta"]["units"] == {"factor": "kg CO2e/kWh"}


def test_table_duplicate_keys_block(store):
    rows = pl.DataFrame({"id": ["a", "a"], "group": ["x", "x"], "factor": [0.1, 0.2]})
    install("fake_table_dupes", RESOURCES={"t": "https://example.org/t.csv"}, transform=lambda raw: rows)
    c = contract(kind="table", pipeline="fake_table_dupes", schema=TABLE_SCHEMA, key=["id"])
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/t.csv": b"x"}), now=NOW)
    assert result.outcome == "blocked"
    assert any("share a key" in e for e in result.report.errors)


def test_table_contract_needs_key():
    with pytest.raises(ContractError, match="needs a 'key'"):
        contract(kind="table", pipeline="x", schema=TABLE_SCHEMA)


# --- files kind --------------------------------------------------------------------------------


def test_files_dataset_publishes_built_files_and_checks_required(store):
    built = {"boundaries/states.geojson": b'{"type":"FeatureCollection","features":[]}', "README.txt": b"hi"}
    install("fake_files", RESOURCES={"s": "https://example.org/s.zip"}, build=lambda raw: (built, {"rows": 56}))
    c = contract(kind="files", pipeline="fake_files", checks={"required_files": ["boundaries/states.geojson"], "min_rows": 50})
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/s.zip": b"zip"}), now=NOW)
    assert result.outcome == "published"
    doc = doc_of(store, c, result.version)
    assert doc["files"]["boundaries/states.geojson"]["type"] == "application/geo+json"
    assert doc["coverage"] == {"start": None, "end": None}

    install("fake_files_missing", RESOURCES={"s": "https://example.org/s.zip"}, build=lambda raw: ({"other.json": b"{}"}, {"rows": 1}))
    c2 = contract(id="other", kind="files", pipeline="fake_files_missing", checks={"required_files": ["boundaries/states.geojson"], "min_rows": 50})
    blocked = publish.run_dataset(c2, store, fetcher=static_fetcher({"https://example.org/s.zip": b"zip"}), now=NOW)
    assert blocked.outcome == "blocked"
    assert any("required files missing" in e for e in blocked.report.errors)
    assert any("requires at least 50" in e for e in blocked.report.errors)


# --- series locations --------------------------------------------------------------------------

SERIES_SCHEMA = [{"name": "date", "type": "month"}, {"name": "value", "type": "float"}]


def test_registry_locations_get_names_from_geo(store):
    rows = pl.DataFrame({"series": ["t", "t"], "location": ["us-ca", "us-tx"], "date": ["2026-09", "2026-09"], "value": [1.0, 2.0]})
    install("fake_registry", RESOURCES={"r": "https://example.org/r"}, transform=lambda raw: rows)
    c = contract(pipeline="fake_registry", schema=SERIES_SCHEMA, series={"t": "Temp"}, locations={"registry": "us-state"})
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/r": b"r"}), now=NOW)
    assert result.outcome == "published"
    assert doc_of(store, c, result.version)["locations"] == {"us-ca": "California", "us-tx": "Texas"}


def test_registry_locations_reject_unknown_ids(store):
    rows = pl.DataFrame({"series": ["t"], "location": ["us-zz"], "date": ["2026-09"], "value": [1.0]})
    install("fake_registry_bad", RESOURCES={"r": "https://example.org/r"}, transform=lambda raw: rows)
    c = contract(pipeline="fake_registry_bad", schema=SERIES_SCHEMA, series={"t": "Temp"}, locations={"registry": "us-state"})
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/r": b"r"}), now=NOW)
    assert result.outcome == "blocked"
    assert any("not allowed" in e and "us-zz" in e for e in result.report.errors)


def test_pattern_locations_take_names_from_the_transform(store):
    rows = pl.DataFrame({
        "series": ["q", "q"], "location": ["usgs-01646500", "usgs-0000"], "location_name": ["Potomac R near Wash DC", "bad"],
        "date": ["2026-09", "2026-09"], "value": [1.0, 2.0],
    })
    install("fake_pattern", RESOURCES={"r": "https://example.org/r"}, transform=lambda raw: rows)
    c = contract(pipeline="fake_pattern", schema=SERIES_SCHEMA, series={"q": "Flow"}, locations={"pattern": r"usgs-\d{8,15}"})
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/r": b"r"}), now=NOW)
    assert result.outcome == "blocked"  # usgs-0000 doesn't match the pattern
    ok_rows = rows.filter(pl.col("location") != "usgs-0000")
    install("fake_pattern_ok", RESOURCES={"r": "https://example.org/r"}, transform=lambda raw: ok_rows)
    c2 = contract(id="ok", pipeline="fake_pattern_ok", schema=SERIES_SCHEMA, series={"q": "Flow"}, locations={"pattern": r"usgs-\d{8,15}"})
    ok = publish.run_dataset(c2, store, fetcher=static_fetcher({"https://example.org/r": b"r"}), now=NOW)
    doc = doc_of(store, c2, ok.version)
    assert doc["locations"] == {"usgs-01646500": "Potomac R near Wash DC"}
    meta = json.loads(store.get(blob_key(doc["files"]["q/usgs-01646500.json"]["blob"]))[0])["meta"]
    assert meta["location_name"] == "Potomac R near Wash DC"


# --- sharding ----------------------------------------------------------------------------------


def test_big_datasets_shard_their_file_map_and_locations(store, monkeypatch):
    monkeypatch.setattr(publish, "SHARD_THRESHOLD", 4)
    monkeypatch.setattr(publish, "LOCATIONS_INLINE_MAX", 2)
    locs = ["us-ca", "us-tx", "us-ny"]
    rows = pl.DataFrame({
        "series": [s for s in ("a", "b") for _ in locs], "location": locs * 2,
        "date": ["2026-09"] * 6, "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    install("fake_shard", RESOURCES={"r": "https://example.org/r"}, transform=lambda raw: rows)
    c = contract(pipeline="fake_shard", schema=SERIES_SCHEMA, series={"a": "A", "b": "B"}, locations={"registry": "us-state"})
    result = publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/r": b"r"}), now=NOW)
    doc = doc_of(store, c, result.version)
    assert sorted(doc["files"]) == ["download/thing.csv", "download/thing.parquet"]
    assert sorted(doc["file_shards"]) == ["a", "b"]
    shard_a = json.loads(store.get(blob_key(doc["file_shards"]["a"]))[0])
    assert sorted(shard_a) == sorted(f"a/{loc}.{ext}" for loc in locs for ext in ("csv", "json"))
    assert "locations" not in doc
    assert json.loads(store.get(blob_key(doc["locations_blob"]))[0])["us-ny"] == "New York"
    # The tree hash still covers every file, so an identical rebuild is recognized as unchanged.
    all_files = {**doc["files"], **{p: e for s in doc["file_shards"].values() for p, e in json.loads(store.get(blob_key(s))[0]).items()}}
    assert publish.tree_hash({p: e["blob"] for p, e in all_files.items()}) == doc["tree"]


# --- collect() and the raw-archive cap ---------------------------------------------------------


def test_collect_pipelines_detect_unchanged_content(store):
    pages = {"page-1": b"a,b\n1,2\n"}
    install(
        "fake_collect",
        collect=lambda ctx: dict(pages),
        transform=lambda raw: pl.DataFrame({"series": ["t"], "location": ["us-ca"], "date": ["2026-09"], "value": [1.0]}),
    )
    c = contract(pipeline="fake_collect", schema=SERIES_SCHEMA, series={"t": "T"}, locations={"registry": "us-state"})
    assert publish.run_dataset(c, store, now=NOW).outcome == "published"
    assert publish.run_dataset(c, store, now=NOW).outcome == "unchanged"


def test_oversized_raw_is_not_archived_and_is_refetched_when_needed(store):
    url = "https://example.org/huge.zip"
    install(
        "fake_huge",
        RESOURCES={"h": url},
        transform=lambda raw: pl.DataFrame({"series": ["t"], "location": ["us-ca"], "date": ["2026-09"], "value": [float(len(raw["h"]))]}),
    )
    fetcher = static_fetcher({url: b"0123456789"})
    c = contract(pipeline="fake_huge", schema=SERIES_SCHEMA, series={"t": "T"}, locations={"registry": "us-state"}, raw_archive_max_mb=0)
    first = publish.run_dataset(c, store, fetcher=fetcher, now=NOW)
    assert first.outcome == "published"
    assert not store.list("raw/test-source/")  # too big to archive
    # A contract change forces a rebuild while the server says 304: the body must be refetched.
    c2 = contract(pipeline="fake_huge", schema=SERIES_SCHEMA, series={"t": "T"}, locations={"registry": "us-state"}, raw_archive_max_mb=0, version=2)
    again = publish.run_dataset(c2, store, fetcher=fetcher, now=NOW)
    assert again.outcome == "unchanged"  # same bytes out
    assert fetcher.calls[-2][1] is not None and fetcher.calls[-1] == (url, None)  # conditional 304, then unconditional refetch


def test_build_gets_a_read_only_context_with_previous_files(store):
    seen = {}

    def build(raw, ctx):
        seen["previous"] = ctx.previous_files()
        prior = seen["previous"].get("keep.txt")
        seen["prior_body"] = ctx.read_blob(prior["blob"]) if prior else None
        return {"keep.txt": raw["s"], "README.txt": b"x"}, {"rows": 1}

    install("fake_files_ctx", RESOURCES={"s": "https://example.org/s"}, build=build)
    c = contract(kind="files", pipeline="fake_files_ctx", checks={"required_files": ["keep.txt"]})
    publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/s": b"v1"}), now=NOW)
    assert seen["previous"] == {} and seen["prior_body"] is None
    c2 = contract(kind="files", pipeline="fake_files_ctx", checks={"required_files": ["keep.txt"]}, version=2)
    publish.run_dataset(c2, store, fetcher=static_fetcher({"https://example.org/s": b"v2"}), now=NOW)
    assert "keep.txt" in seen["previous"] and seen["prior_body"] == b"v1"


def test_manifest_written_once_per_publish(store):
    install("fake_once", RESOURCES={"r": "https://example.org/r"},
            transform=lambda raw: pl.DataFrame({"series": ["t"], "location": ["us-ca"], "date": ["2026-09"], "value": [1.0]}))
    c = contract(pipeline="fake_once", schema=SERIES_SCHEMA, series={"t": "T"}, locations={"registry": "us-state"})
    publish.run_dataset(c, store, fetcher=static_fetcher({"https://example.org/r": b"r"}), now=NOW)
    assert get_json(store, manifest_key(c.site, c.id))[0]["rev"] == 1


# --- per-series units --------------------------------------------------------------------------

CLIMATE_LIKE = {
    "pipeline": "x",
    "series": {"tavg": "Average temperature", "pcpn": "Precipitation"},
    "series_units": {"tavg": "degF", "pcpn": "in"},
    "locations": {"explicit": {"us-md": "Maryland"}},
    "schema": [
        {"name": "date", "type": "month"},
        {"name": "value", "type": "float", "unit": "degF or in, by series"},
        {"name": "rank", "type": "int"},
    ],
}


def test_series_units_label_each_series_file():
    from datasites_core.outputs import iter_series_outputs

    c = contract(**CLIMATE_LIKE)
    table = pl.DataFrame(
        {"series": ["pcpn", "tavg"], "location": ["us-md", "us-md"], "date": ["2026-09", "2026-09"], "value": [3.1, 71.5], "rank": [4, 9]}
    )
    files = dict(iter_series_outputs(c, table, {"us-md": "Maryland"}))
    meta = lambda path: json.loads(files[path].body)["meta"]  # noqa: E731
    assert meta("tavg/us-md.json")["units"] == {"value": "degF"}
    assert meta("pcpn/us-md.json")["units"] == {"value": "in"}  # rank has no unit, so it stays out


def test_series_units_must_name_known_series():
    with pytest.raises(ContractError, match="series_units names series not in 'series'"):
        contract(**{**CLIMATE_LIKE, "series_units": {"snow": "in"}})
