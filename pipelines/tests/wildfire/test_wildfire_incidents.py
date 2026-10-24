"""fire-incidents and the WFIGS resultOffset paging shared by every WFIGS dataset."""

import json

import pytest
from wildfire_helpers import INCIDENTS_COUNT, NOW, FakeGet, Upstream, count_url, fixture, relaxed

from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.publish import run_dataset
from datasites_wildfire import _common, wfigs_incidents


def page(name):
    return {"page-000": fixture(name)}


def test_page_count_keeps_a_margin():
    assert _common.page_count(648, 2000) == 1
    assert _common.page_count(1950, 2000) == 2  # within 10% of a full page: one more page
    assert _common.page_count(0, 2000) == 1
    assert _common.page_count(648, 250) == 3


def test_page_urls_use_result_offset_ordered_by_objectid(monkeypatch):
    fake = FakeGet({count_url(INCIDENTS_COUNT): fixture("paging-count.json")})
    monkeypatch.setattr(CollectContext, "get", fake)
    ctx = CollectContext(load_contract("wildfiredata/fire-incidents"), NOW)
    urls = _common.page_urls(ctx, _common.INCIDENTS_LAYER, wfigs_incidents.QUERY, 250)
    assert list(urls) == ["page-000", "page-001", "page-002"]  # 648 records + margin
    for i, url in enumerate(urls.values()):
        assert f"resultOffset={i * 250}&resultRecordCount=250" in url
        assert "orderByFields=OBJECTID+ASC" in url and "f=json" in url and "outSR=4326" in url
    assert len(fake.calls) == 1 and "returnCountOnly=true" in fake.calls[0]


def test_reading_real_pages_in_order():
    raw = {"page-000": fixture("paging-offset-000.json"), "page-001": fixture("paging-offset-002.json"), "page-002": fixture("paging-offset-647.json")}
    features = _common.read_pages(raw)
    assert [f["attributes"]["OBJECTID"] for f in features] == [519515, 520087, 520527, 522842, 603793]


def test_overlapping_pages_do_not_duplicate_records():
    raw = {"page-000": fixture("paging-offset-000.json"), "page-001": fixture("paging-offset-000.json"), "page-002": fixture("paging-offset-647.json")}
    assert len(_common.read_pages(raw)) == 3


def test_a_full_last_page_means_the_layer_grew():
    raw = {"page-000": fixture("paging-offset-000.json"), "page-001": fixture("paging-offset-002.json")}
    with pytest.raises(RuntimeError, match="grew while paging"):
        _common.read_pages(raw)


def test_error_bodies_raise():
    with pytest.raises(RuntimeError, match="returned an error"):
        _common.read_pages({"page-000": b'{"error":{"code":500,"message":"Error performing query operation","details":[]}}'})
    with pytest.raises(ValueError, match="no 'features'"):
        _common.read_pages({"page-000": b'{"count":648}'})


def test_count_errors_raise(monkeypatch):
    monkeypatch.setattr(CollectContext, "get", FakeGet({count_url(INCIDENTS_COUNT): b'{"error":{"code":499,"message":"Token Required"}}'}))
    with pytest.raises(RuntimeError, match="count request failed"):
        _common.page_urls(CollectContext(load_contract("wildfiredata/fire-incidents"), NOW), _common.INCIDENTS_LAYER, {}, 2000)


def test_transform_cleans_the_records():
    table = wfigs_incidents.transform(page("incidents-page-000.json"))
    contract = load_contract("wildfiredata/fire-incidents")
    assert table.columns == contract.columns
    assert table.height == 15 and table["irwin_id"].n_unique() == 15
    raw = json.loads(fixture("incidents-page-000.json"))
    source = next(f["attributes"] for f in raw["features"] if f["attributes"]["IncidentName"] == "LITTLE GIANT")
    row = table.filter(table["name"] == "LITTLE GIANT").row(0, named=True)
    assert row["irwin_id"] == source["IrwinID"].strip("{}")
    assert row["acres"] == source["IncidentSize"] == 172912
    assert row["percent_contained"] == source["PercentContained"]
    assert row["state"] == "us-wa" and row["type"] == "WF"
    assert row["discovered"] == "2026-07-16T01:17:54Z"  # 1784164674000 ms after the epoch, UTC
    assert row["unique_fire_id"] == "2026-WAOWF-260406"
    assert all(len(f) == 5 for f in table["county_fips"].drop_nulls())
    assert table["latitude"].is_between(13, 72).all()


def test_duplicate_irwin_ids_keep_the_latest_record():
    raw = json.loads(fixture("incidents-page-000.json"))
    first = raw["features"][0]
    stale = json.loads(json.dumps(first))
    stale["attributes"]["OBJECTID"] = 1
    stale["attributes"]["ModifiedOnDateTime_dt"] -= 86_400_000
    stale["attributes"]["IncidentSize"] = -1
    raw["features"].append(stale)
    table = wfigs_incidents.transform({"page-000": json.dumps(raw).encode()})
    row = table.filter(table["irwin_id"] == first["attributes"]["IrwinID"].strip("{}")).row(0, named=True)
    assert table.height == 15 and row["acres"] == first["attributes"]["IncidentSize"]


def test_end_to_end_and_validation(store, monkeypatch):
    monkeypatch.setattr(CollectContext, "get", FakeGet({count_url(INCIDENTS_COUNT): fixture("paging-count.json")}))
    contract = relaxed("wildfiredata/fire-incidents")
    urls = wfigs_incidents.resources(CollectContext(contract, NOW))
    upstream = Upstream({urls["page-000"]: fixture("incidents-page-000.json")})
    result = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert result.outcome == "published", result.report.errors if result.report else None
    # An incident size that is not a fire size (a unit slip upstream) blocks the publish.
    body = fixture("incidents-page-000.json").replace(b"\"IncidentSize\":172912,", b"\"IncidentSize\":172912000,")
    upstream.bodies[urls["page-000"]] = body
    blocked = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert blocked.outcome == "blocked"
    assert any(e.startswith("acres:") for e in blocked.report.errors)
