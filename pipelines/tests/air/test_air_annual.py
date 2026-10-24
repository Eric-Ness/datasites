import json

import pytest
from air_helpers import (
    ANNUAL_YEARS,
    NOW,
    FakeGet,
    Upstream,
    airdata_zip,
    annual_contract,
    annual_raw,
    fixture,
    rows,
)

from datasites_air import county_aqi_annual
from datasites_air.aqi import AIRDATA
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.publish import run_dataset
from datasites_core.validate import validate


@pytest.fixture(scope="module")
def table():
    return county_aqi_annual.transform(annual_raw())


def test_shape_and_validation(table):
    assert table.columns == load_contract("airdata/county-aqi-annual").columns
    assert set(table["series"]) == {"summary"}
    report = validate(annual_contract(), table, None)
    assert report.errors == [] and report.warnings == []


def test_values_are_published_as_received(table):
    assert rows(table, "us-county-06037")["2025"] == {
        "series": "summary",
        "location": "us-county-06037",
        "date": "2025",
        "days_with_aqi": 365,
        "good_days": 39,
        "moderate_days": 228,
        "unhealthy_sensitive_days": 66,
        "unhealthy_days": 31,
        "very_unhealthy_days": 1,
        "hazardous_days": 0,
        "max_aqi": 201,
        "aqi_90th_percentile": 147,
        "median_aqi": 74,
        "days_co": 0,
        "days_no2": 6,
        "days_ozone": 193,
        "days_pm25": 151,
        "days_pm10": 15,
    }


def test_county_names_map_to_fips_through_aqs_sites(table):
    by_location = {loc for loc in table["location"]}
    # independent cities and their namesake counties keep separate codes
    assert (
        rows(table, "us-county-24510")["2025"]["max_aqi"] == 136
    )  # "Baltimore (City)"
    assert (
        rows(table, "us-county-24005")["2025"]["max_aqi"] == 129
    )  # "Baltimore" county
    assert rows(table, "us-county-29510")["2025"]["max_aqi"] == 738  # "St. Louis City"
    assert (
        rows(table, "us-county-29189")["2025"]["max_aqi"] == 151
    )  # "Saint Louis" county
    # Connecticut's former county, a retired Alaska census area with a padded name ("Valdez-Cordova "), Puerto Rico
    assert rows(table, "us-county-09001")["2025"]["days_with_aqi"] == 365
    assert rows(table, "us-county-02261")["1980"]["days_with_aqi"] == 361
    assert rows(table, "us-county-72033")["2025"]["days_pm25"] == 328
    # Mexico's border "counties" are dropped
    assert not any(loc.startswith("us-county-80") for loc in by_location)


def test_a_county_name_missing_from_aqs_sites_stops_the_run():
    raw = annual_raw()
    sites = fixture("aqs_sites.csv")
    raw["aqs-sites"] = airdata_zip(
        "aqs_sites.csv",
        b"\n".join(ln for ln in sites.split(b"\n") if b'"Baltimore (City)"' not in ln),
    )
    with pytest.raises(ValueError, match=r"Baltimore \(City\)"):
        county_aqi_annual.transform(raw)


def test_inconsistent_day_counts_stop_the_run():
    raw = annual_raw()
    body = fixture("annual_aqi_by_county_2025.csv").replace(
        b'"California","Los Angeles",2025,365,39,',
        b'"California","Los Angeles",2025,365,40,',
    )
    raw["airdata-2025"] = airdata_zip("annual_aqi_by_county_2025.csv", body)
    with pytest.raises(ValueError, match="add up"):
        county_aqi_annual.transform(raw)


def _bodies() -> dict[str, bytes]:
    bodies = {
        f"{AIRDATA}/annual_aqi_by_county_{y}.zip": airdata_zip(
            f"annual_aqi_by_county_{y}.csv"
        )
        for y in ANNUAL_YEARS
    }
    bodies[f"{AIRDATA}/aqs_sites.zip"] = airdata_zip("aqs_sites.csv")
    return bodies


def _run(store, upstream, monkeypatch):
    FakeGet().install(monkeypatch)
    monkeypatch.setattr(
        county_aqi_annual, "airdata_years", lambda page, prefix: list(ANNUAL_YEARS)
    )
    return run_dataset(annual_contract(), store, fetcher=upstream, now=NOW)


def test_resources(monkeypatch):
    FakeGet().install(monkeypatch)
    res = county_aqi_annual.resources(
        CollectContext(load_contract("airdata/county-aqi-annual"), NOW)
    )
    assert res["airdata-1980"] == f"{AIRDATA}/annual_aqi_by_county_1980.zip"
    assert res["airdata-2026"] == f"{AIRDATA}/annual_aqi_by_county_2026.zip"
    assert res["aqs-sites"] == f"{AIRDATA}/aqs_sites.zip"
    assert len(res) == 48


def test_end_to_end_publishes_then_is_unchanged(store, monkeypatch):
    upstream = Upstream(_bodies())
    first = _run(store, upstream, monkeypatch)
    assert first.outcome == "published", first.report
    manifest = json.loads(
        store.get("sites/airdata/datasets/county-aqi-annual/manifest.json")[0]
    )
    doc = json.loads(
        store.get(
            f"sites/airdata/datasets/county-aqi-annual/versions/{manifest['current']}.json"
        )[0]
    )
    assert doc["coverage"] == {"start": "1980", "end": "2025"}
    assert doc["source"]["commercial_ok"] is True
    upstream.requests.clear()
    assert _run(store, upstream, monkeypatch).outcome == "unchanged"
    assert all(etag for _url, etag in upstream.requests)


def test_validation_blocks_an_out_of_range_day_count(store, monkeypatch):
    upstream = Upstream(_bodies())
    assert _run(store, upstream, monkeypatch).outcome == "published"
    # a reissue with a day count no year has (a realistic slip: a leap-day double count)
    body = fixture("annual_aqi_by_county_2025.csv").replace(
        b'"Missouri","Saint Louis",2025,365,268,91,5,1,0,0,151,61,43,0,16,191,158,0',
        b'"Missouri","Saint Louis",2025,367,270,91,5,1,0,0,151,61,43,0,16,193,158,0',
    )
    upstream.bodies[f"{AIRDATA}/annual_aqi_by_county_2025.zip"] = airdata_zip(
        "annual_aqi_by_county_2025.csv", body
    )
    result = _run(store, upstream, monkeypatch)
    assert result.outcome == "blocked"
    assert any("days_with_aqi" in e for e in result.report.errors)
