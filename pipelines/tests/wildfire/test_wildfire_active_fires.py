"""active-fires: events from the real FIRMS excerpt, regions, incident links and an end-to-end run."""

import json

import polars as pl
import pytest
from wildfire_helpers import (
    INCIDENTS_COUNT,
    NOW,
    PERIMETERS_COUNT,
    FakeGet,
    Upstream,
    active_fires_raw,
    count_url,
    fixture,
    relaxed,
)

from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.layout import blob_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_wildfire import active_fires, admin1, firms


@pytest.fixture(scope="module")
def table():
    return active_fires.transform(active_fires_raw())


def by_incident(table, name):
    rows = table.filter(pl.col("incident_name") == name)
    assert len(rows) == 1, f"{name}: {len(rows)} events"
    return rows.row(0, named=True)


def test_schema_matches_the_contract(table):
    contract = load_contract("wildfiredata/active-fires")
    assert table.columns == contract.columns
    assert table["event_id"].n_unique() == len(table)
    assert set(table["country"]) == {"usa", "can"}


def test_events_in_the_excerpt(table):
    # Three Oregon prescribed burns and a small unlinked event, COOLEY in California, two Prudhoe Bay
    # flare events in Alaska split by a 51-hour gap, and the Hamilton, Ontario steelworks.
    counts = table.group_by("region").len().sort("region").rows()
    assert counts == [("ca-on", 1), ("us-ak", 2), ("us-ca", 1), ("us-or", 4)]
    hamilton = table.filter(pl.col("region") == "ca-on").row(0, named=True)
    assert hamilton["region_name"] == "Ontario" and hamilton["country"] == "can"
    assert hamilton["incident_irwin_id"] is None  # WFIGS covers the US only


def test_perimeter_link(table):
    cooley = by_incident(table, "COOLEY")
    assert cooley["incident_link"] == "perimeter" and cooley["incident_distance_km"] == 0.0
    assert cooley["incident_type"] == "WF" and cooley["incident_acres"] == 1365.0
    assert cooley["region"] == "us-ca" and cooley["region_name"] == "California"
    assert cooley["incident_irwin_id"] == cooley["incident_irwin_id"].upper() and "{" not in cooley["incident_irwin_id"]


def test_origin_links_need_the_origin_close_to_a_detection(table):
    jane = by_incident(table, "Jane 102 RX")
    assert jane["incident_link"] == "origin" and 0 < jane["incident_distance_km"] < 1
    assert jane["incident_type"] == "RX" and jane["incident_acres"] == 3000.0
    assert jane["incident_discovered"] == "2026-09-30T15:51:00Z"
    unlinked = table.filter((pl.col("region") == "us-or") & pl.col("incident_irwin_id").is_null())
    assert len(unlinked) == 1 and unlinked["raw_detections"][0] == 6


def test_event_values_against_the_raw_rows(table):
    """Recount one event from the CSV rows with plain Python: rows inside its bounding box and time span."""
    jane = by_incident(table, "Jane 102 RX")
    seen = set()
    frp = []
    for name, body in active_fires_raw().items():
        if not name.startswith("firms-"):
            continue
        header, *lines = body.decode().strip().split("\n")
        cols = header.split(",")
        for line in lines:
            row = dict(zip(cols, line.split(",")))
            lat, lon = float(row["latitude"]), float(row["longitude"])
            t = f"{row['acq_date']}T{row['acq_time'][:2]}:{row['acq_time'][2:]}:00Z"
            inside = jane["min_latitude"] <= lat <= jane["max_latitude"] and jane["min_longitude"] <= lon <= jane["max_longitude"]
            if inside and jane["first_detection"] <= t <= jane["last_detection"]:
                key = (row["satellite"], t, lat, lon)
                if key not in seen:
                    seen.add(key)
                    frp.append(float(row["frp"]))
    assert len(seen) == jane["raw_detections"] == 469
    assert max(frp) == jane["max_frp"]
    assert jane["detections"] < jane["raw_detections"]
    assert jane["satellites"] == "S-NPP,NOAA-20,NOAA-21"


def test_static_source_heuristic_and_dateline_bbox():
    lat, lon = 70.31, -148.5
    rows = [(lat, lon, f"2026-10-{d}", "1323", "N", 1.0) for d in range(17, 24)]
    body = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"
            + "\n".join(f"{a},{b},330,0.4,0.4,{d},{t},{s},nominal,2.0NRT,290,{f},N" for a, b, d, t, s, f in rows)).encode()
    raw = {"firms-x": body, "admin1": fixture("ne_10m_admin_1_excerpt.zip"),
           "incidents-page-000": fixture("af-incidents-page-000.json"), "perimeters-page-000": fixture("af-perimeters-page-000.json")}
    flare = active_fires.transform(raw).row(0, named=True)
    assert flare["days_detected"] == 7 and flare["likely_static_source"] is True
    det = firms.cluster(firms.mark_duplicates(firms.detections({"firms-x": (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"
        "51.8,179.9995,330,0.4,0.4,2026-10-20,1000,N,nominal,2.0NRT,290,1.0,D\n51.8,-179.9995,330,0.4,0.4,2026-10-20,1000,N,nominal,2.0NRT,290,2.0,D\n").encode()})))
    ev = active_fires.events(det).row(0, named=True)
    assert ev["min_longitude"] == 179.9995 and ev["max_longitude"] == -179.9995  # crosses the 180th meridian
    assert abs(abs(ev["longitude"]) - 180) < 1e-3


def test_regions_include_the_aleutians_and_reject_mexico():
    pts = pl.DataFrame({"pid": [0, 1, 2, 3], "latitude": [51.88, 52.93, 44.03, 46.6], "longitude": [-176.6, 173.2, -118.77, -124.6]})
    out = admin1.regions(fixture("ne_10m_admin_1_excerpt.zip"), pts).sort("pid")
    # Adak (west of 180 W), Attu (east of 180), Oregon, and a point 15 km off the Washington coast.
    assert out["region"].to_list() == ["us-ak", "us-ak", "us-or", None]


def test_resources_page_both_wfigs_layers(monkeypatch):
    fake = FakeGet({count_url(INCIDENTS_COUNT): fixture("paging-count.json"), count_url(PERIMETERS_COUNT): fixture("perimeters-count.json")})
    monkeypatch.setattr(CollectContext, "get", fake)
    urls = active_fires.resources(CollectContext(load_contract("wildfiredata/active-fires"), NOW))
    assert sorted(n for n in urls if not n.startswith("firms-")) == ["admin1", "incidents-page-000", "perimeters-page-000"]
    assert "resultOffset=0" in urls["perimeters-page-000"] and "resultRecordCount=250" in urls["perimeters-page-000"]
    assert "orderByFields=OBJECTID+ASC" in urls["incidents-page-000"] and "f=json" in urls["incidents-page-000"]
    assert "f=geojson" in urls["perimeters-page-000"] and "maxAllowableOffset=0.0001" in urls["perimeters-page-000"]


def upstream_and_get(monkeypatch, raw):
    fake = FakeGet({count_url(INCIDENTS_COUNT): fixture("paging-count.json"), count_url(PERIMETERS_COUNT): fixture("perimeters-count.json")})
    monkeypatch.setattr(CollectContext, "get", fake)
    urls = active_fires.resources(CollectContext(load_contract("wildfiredata/active-fires"), NOW))
    return Upstream({urls[name]: body for name, body in raw.items()}), urls


def test_end_to_end_into_the_store(store, monkeypatch):
    upstream, _ = upstream_and_get(monkeypatch, active_fires_raw())
    contract = relaxed("wildfiredata/active-fires")
    result = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert result.outcome == "published", result.report.errors if result.report else None
    doc = get_json(store, f"sites/wildfiredata/datasets/active-fires/versions/{result.version}.json")[0]
    assert sorted(doc["files"]) == [
        "download/active-fires.csv", "download/active-fires.parquet", "table/all.csv", "table/all.json",
        "table/can.csv", "table/can.json", "table/usa.csv", "table/usa.json",
    ]
    can = json.loads(store.get(blob_key(doc["files"]["table/can.json"]["blob"]))[0])
    assert can["meta"]["partition"] == {"country": "can"} and can["meta"]["rows"] == 1
    assert doc["source"]["attribution"].startswith("We acknowledge the use of imagery from the NASA LANCE FIRMS")
    # Coverage is measured on first_detection, the first time column: the newest event start is the end.
    assert doc["coverage"] == {"start": "2026-10-17T09:24:00Z", "end": "2026-10-21T16:24:00Z"}
    again = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert again.outcome == "unchanged"


def test_header_only_firms_files_block_publishing(store, monkeypatch):
    """A FIRMS outage serves header-only files: the empty table must not replace the published one."""
    raw = active_fires_raw()
    upstream, urls = upstream_and_get(monkeypatch, raw)
    contract = load_contract("wildfiredata/active-fires")
    first = run_dataset(relaxed("wildfiredata/active-fires"), store, fetcher=upstream, now=NOW)
    assert first.outcome == "published"
    for name, body in raw.items():
        if name.startswith("firms-"):
            upstream.bodies[[u for u in upstream.bodies if upstream.bodies[u] is body][0]] = body.split(b"\n", 1)[0] + b"\n"
    blocked = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert blocked.outcome == "blocked"
    assert any("empty" in e for e in blocked.report.errors)
    manifest = get_json(store, "sites/wildfiredata/datasets/active-fires/manifest.json")[0]
    assert manifest["current"] == first.version


def test_too_few_events_block(store, monkeypatch):
    upstream, _ = upstream_and_get(monkeypatch, active_fires_raw())
    blocked = run_dataset(load_contract("wildfiredata/active-fires"), store, fetcher=upstream, now=NOW)
    assert blocked.outcome == "blocked"
    assert any("requires at least 25" in e for e in blocked.report.errors)


def test_wfigs_error_bodies_are_errors_not_empty_data():
    raw = active_fires_raw()
    raw["incidents-page-000"] = b'{"error":{"code":400,"message":"Cannot perform query. Invalid query parameters.","details":[]}}'
    with pytest.raises(RuntimeError, match="returned an error"):
        active_fires.transform(raw)
