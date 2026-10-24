import json

import polars as pl
import pytest

from datasites_core.contracts import load_contract
from datasites_core.publish import run_dataset
from datasites_water import usgs, usgs_gages
from water_helpers import NOW, FakeCtx, FakeUSGS, fixture, published_file, query, relaxed, usgs_raw, version_doc

IN_WINDOW = {
    "usgs-01360640",
    "usgs-01574500",
    "usgs-01634500",
    "usgs-01646500",
    "usgs-02290765",
    "usgs-03294500",
    "usgs-05102500",
    "usgs-16809600",
}


def gage_rows(raw=None) -> dict[str, dict]:
    table = usgs_gages.transform(raw or usgs_raw())
    return {row["site_id"]: row for row in table.iter_rows(named=True)}


def test_columns_match_the_contract():
    assert usgs_gages.transform(usgs_raw()).columns == load_contract("waterdata/stream-gages").columns


def test_only_usgs_stream_gages_reporting_in_the_window():
    rows = gage_rows()
    # Excluded: a spring (SP), a gage whose daily record ended in 2020, and a gage another agency operates.
    assert set(rows) == IN_WINDOW
    assert "usgs-02235500" not in rows and "usgs-09419659" not in rows
    assert not any(site.startswith("ca574") for site in rows)


def test_potomac_attributes():
    assert gage_rows()["usgs-01646500"] == {
        "site_id": "usgs-01646500",
        "site_number": "01646500",
        "name": "POTOMAC RIVER NEAR WASH, DC LITTLE FALLS PUMP STA",
        "site_type_code": "ST",
        "site_type": "Stream",
        "region": "us-md",
        "country": "usa",
        "state": "MD",
        "county_fips": "24031",
        "county_name": "Montgomery County",
        "huc": "020700081005",
        "huc8": "02070008",
        "latitude": 38.949777777777776,
        "longitude": -77.12763888888888,
        "drainage_area": 11560.0,
        "contributing_drainage_area": None,
        "altitude": 37.04,
        "altitude_datum": "NAVD88",
        "time_zone": "EST",
        "time_series_id": "2cf409b0d9694c01828f5d4ff14586a5",
        "sublocation": None,
        "daily_begin": "1930-03-01",
        "daily_end": "2026-10-23",
    }


def test_the_main_series_wins_over_a_series_adjusted_for_diversions():
    # Codorus Creek has two Primary daily series; the second is described "Daily mean with diversions".
    assert gage_rows()["usgs-01574500"]["time_series_id"] == "0a20842f14cd4b4ca2de9356128724de"


def test_gage_in_canada_has_no_us_state_or_county():
    row = gage_rows()["usgs-05102500"]
    assert (row["region"], row["country"], row["state"], row["county_fips"], row["county_name"]) == ("can", "can", None, None, None)
    assert row["huc"] is None and row["huc8"] is None
    assert row["drainage_area"] == 40200.0


def test_eight_digit_huc_and_missing_drainage_area():
    row = gage_rows()["usgs-02290765"]
    assert row["huc"] == "03090202" and row["huc8"] == "03090202"
    assert row["drainage_area"] is None and row["altitude"] is None and row["altitude_datum"] is None


def test_local_dates_for_a_gage_east_of_utc():
    # Guam (UTC+10): local midnight is 14:00 UTC the day before, e.g. '2026-10-21 14:00:00+00:00'.
    row = gage_rows()["usgs-16809600"]
    assert (row["daily_begin"], row["daily_end"]) == ("1953-04-30", "2026-10-22")
    assert (row["region"], row["state"], row["county_fips"]) == ("us-gu", "GU", "66010")


def test_window_cut_uses_the_local_end_date():
    raw = usgs_raw()
    raw["query"] = query(start="2026-01-01")
    assert "usgs-05102500" not in gage_rows(raw)  # its record ends 2025-12-31
    raw["query"] = query(start="2025-12-31")
    assert "usgs-05102500" in gage_rows(raw)


def test_unexpected_units_are_refused():
    raw = usgs_raw()
    raw["time-series"] = raw["time-series"].replace(b"USGS-01646500,ft^3/s", b"USGS-01646500,m^3/s", 1)
    with pytest.raises(ValueError, match="unexpected discharge units"):
        usgs_gages.transform(raw)


def test_collect_queries_recent_series_then_locations_in_batches(monkeypatch):
    fake = FakeUSGS().install(monkeypatch)
    monkeypatch.setattr(usgs, "ID_BATCH_CHARS", 41)  # three 13-character ids
    raw = usgs_gages.collect(FakeCtx())
    assert json.loads(raw["query"]) == {"window_start": "2025-09-20", "window_end": "2026-10-24"}
    series_request = fake.requests[0]
    assert series_request[1]["filter"] == "end >= '2025-09-18'"
    # 10 USGS ids (the CA574 series is not looked up), 3 per request.
    assert sorted(n for n in raw if n.startswith("locations-")) == ["locations-000", "locations-001", "locations-002", "locations-003"]
    looked_up = [p["id"] for u, p, h in fake.requests if "monitoring-locations" in u]
    assert all(len(ids) <= 41 for ids in looked_up) and not any("CA574" in ids for ids in looked_up)
    assert set(usgs_gages.transform(raw)["site_id"]) == IN_WINDOW


def test_api_key_is_optional_and_sent_as_a_header(monkeypatch):
    fake = FakeUSGS().install(monkeypatch)
    monkeypatch.delenv(usgs.KEY_ENV, raising=False)
    usgs_gages.collect(FakeCtx())
    assert all(h == {} for _, _, h in fake.requests)
    monkeypatch.setenv(usgs.KEY_ENV, "test-key-not-real")
    fake.requests.clear()
    raw = usgs_gages.collect(FakeCtx())
    assert all(h == {"X-Api-Key": "test-key-not-real"} for _, _, h in fake.requests)
    assert all("api_key" not in p for _, p, _ in fake.requests)
    assert not any(b"test-key-not-real" in body for body in raw.values())


def test_end_to_end_publishes_partitions_and_blocks_a_partial_fetch(store, monkeypatch):
    contract = relaxed("waterdata/stream-gages", min_rows=5)
    FakeUSGS().install(monkeypatch)
    first = run_dataset(contract, store, now=NOW)
    assert first.outcome == "published", first.report.errors
    doc = version_doc(store, contract, first.version)
    assert doc["partition"] == "region" and doc["source"]["license"] == "LicenseRef-US-Government-Work"
    maryland = json.loads(published_file(store, doc, "table/us-md.json"))
    assert [r["site_id"] for r in maryland["rows"]] == ["usgs-01646500"]
    assert {p for p in doc["files"] if p.startswith("table/") and p.endswith(".json")} == {
        "table/all.json", "table/can.json", "table/us-fl.json", "table/us-gu.json", "table/us-ky.json",
        "table/us-md.json", "table/us-ny.json", "table/us-pa.json", "table/us-va.json",
    }

    # A day when the locations service drops a third of the gages: blocked, the old version keeps serving.
    FakeUSGS(drop_locations={"USGS-01360640", "USGS-01574500", "USGS-01634500"}).install(monkeypatch)
    second = run_dataset(contract, store, now=NOW.replace(day=25))
    assert second.outcome == "blocked"
    assert any("row count fell from 8 to 5" in e for e in second.report.errors)


def test_transform_is_deterministic_and_typed():
    a = usgs_gages.transform(usgs_raw())
    b = usgs_gages.transform(dict(reversed(list(usgs_raw().items()))))
    assert a.sort("site_id").equals(b.sort("site_id"))
    assert a.schema["latitude"] == pl.Float64 and a.schema["daily_end"] == pl.Utf8
    assert fixture("usgs_locations.csv").startswith(b"x,y,id,monitoring_location_name")
