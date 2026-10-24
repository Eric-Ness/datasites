import re

import polars as pl
import pytest
from air_helpers import (
    AIRNOW_DAYS,
    DAILY_YEARS,
    NOW,
    FakeGet,
    Upstream,
    airdata_zip,
    daily_contract,
    daily_raw,
    fixture,
    rows,
)

from datasites_air import airnow, county_aqi_daily
from datasites_air.aqi import AIRDATA
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.publish import read_table, run_dataset
from datasites_core.validate import validate


@pytest.fixture(scope="module")
def table():
    return county_aqi_daily.transform(daily_raw())


def test_shape_and_validation(table):
    contract = load_contract("airdata/county-aqi-daily")
    assert table.columns == contract.columns
    assert set(table["series"]) == {"aqi"}
    report = validate(daily_contract(), table, None)
    assert report.errors == [] and report.warnings == []


# --- county codes -------------------------------------------------------------------------------


def test_validated_rows_are_published_as_received(table):
    la = rows(table, "us-county-06037")
    assert la["2026-04-29"] == {
        "series": "aqi",
        "location": "us-county-06037",
        "date": "2026-04-29",
        "aqi": 87,
        "category": "Moderate",
        "defining_parameter": "Ozone",
        "defining_site": "06-037-0016",
        "sites_reporting": 4,
        "validated": True,
    }
    assert la["2025-12-15"]["aqi"] == 93 and la["2025-12-15"]["sites_reporting"] == 14
    assert la["1980-01-01"]["validated"] is True
    # an extreme Mono Lake dust day: AirData's extrapolated AQI above 500 is kept, not capped
    assert rows(table, "us-county-06051")["2000-11-29"]["aqi"] == 11464
    assert rows(table, "us-county-06051")["2000-11-29"]["category"] == "Hazardous"


def test_connecticut_alaska_and_territories_keep_their_aqs_codes(table):
    locations = set(table["location"])
    # AQS still files Connecticut by its former counties; the contract lists them as legacy locations
    assert {"us-county-09001", "us-county-09015"} <= locations
    assert not any(
        loc.startswith("us-county-091") for loc in locations
    )  # no invented planning-region mapping
    assert (
        rows(table, "us-county-02261")["1980-01-01"]["validated"] is True
    )  # Valdez-Cordova, retired 2019
    assert "us-county-72033" in locations  # Catano, Puerto Rico
    known = load_contract("airdata/county-aqi-daily").locations.names()
    assert {"us-county-09001", "us-county-09015", "us-county-02261"} <= set(known)


def test_mexico_canada_and_maine_mobile_monitors_are_dropped(table):
    locations = set(table["location"])
    assert not any(loc[10:12] in ("80", "CC") for loc in locations)
    assert "us-county-23901" not in locations
    assert all(loc.startswith("us-county-") and len(loc) == 15 for loc in locations)


# --- AirNow sites ---------------------------------------------------------------------------------


def test_airnow_county_values_follow_epa_max_of_sites():
    days = airnow.county_days({"airnow-20260515": fixture("airnow_daily_20260515.dat"),
                               "airnow-sites": fixture("Monitoring_Site_Locations_V2.dat")})  # fmt: skip
    la = days.filter(pl.col("fips") == "06037").row(0, named=True)
    # highest of the LA site AQIs that day: Santa Clarita's 8-hour ozone, 61; eight distinct sites had an AQI
    assert la == {
        "fips": "06037",
        "date": "2026-05-15",
        "aqi": 61,
        "category": "Moderate",
        "defining_parameter": "Ozone",
        "defining_site": "06-037-6012",
        "sites_reporting": 8,
    }


def test_airnow_ids_are_mapped_to_counties_carefully():
    days = airnow.county_days({"airnow-20260415": fixture("airnow_daily_20260415.dat"),
                               "airnow-sites": fixture("Monitoring_Site_Locations_V2.dat")})  # fmt: skip
    by_fips = {r["fips"]: r for r in days.iter_rows(named=True)}
    # Ellensburg's AQS id says Kittitas (53037); the site-locations file's "Chelan" (53007) is not used
    assert by_fips["53037"]["aqi"] == 9 and "53007" not in by_fips
    # Puerto Rico reports under country code 630
    assert by_fips["72033"]["aqi"] == 31
    # the Catawba tribal site gets York County, SC from the site-locations file; 8-hour ozone (54) beats PM2.5 (52)
    assert by_fips["45091"] == {"fips": "45091", "date": "2026-04-15", "aqi": 54, "category": "Moderate",
                                "defining_parameter": "Ozone", "defining_site": "TT0328801", "sites_reporting": 1}  # fmt: skip
    # the Wampanoag monitor comes twice: its tribal id has no county in the locations file (left out, not
    # guessed), and its AQS id 250070001 places it in Dukes County, MA
    assert (
        by_fips["25007"]["defining_site"] == "25-007-0001"
        and by_fips["25007"]["sites_reporting"] == 1
    )
    # temporary monitors (840MM...) and Canadian sites never count
    assert set(by_fips) == {"06037", "53037", "72033", "45091", "25007"}
    # 1-hour ozone and 24-hour SO2 rows carry AQI -999 and are ignored: LA's highest real AQI is 61
    assert by_fips["06037"]["aqi"] == 61


def test_a_tribal_site_also_reported_under_its_aqs_id_counts_once():
    # Were the locations file to give TT0300001 its county (Dukes, MA), the same monitor would appear
    # twice in Dukes; the copy at the AQS site's coordinates is dropped.
    sites = fixture("Monitoring_Site_Locations_V2.dat").replace(
        b"|Wampanoag Tribe|R1|41.330470|-70.785840||-5.00|US||||||",
        b"|Wampanoag Tribe|R1|41.330470|-70.785840||-5.00|US||||25|MA|007|DUKES",
    )
    assert sites != fixture("Monitoring_Site_Locations_V2.dat")
    days = airnow.county_days(
        {"airnow-20260415": fixture("airnow_daily_20260415.dat"), "airnow-sites": sites}
    )
    dukes = days.filter(pl.col("fips") == "25007").row(0, named=True)
    assert dukes["sites_reporting"] == 1 and dukes["defining_site"] == "25-007-0001"


def test_airnow_rows_for_another_day_are_ignored():
    body = fixture("airnow_daily_20260415.dat")
    assert airnow.read_daily(body, "20260416").is_empty()
    assert len(airnow.read_daily(body, "20260415")) > 0


def test_malformed_airnow_lines_are_skipped_not_fatal():
    # A stray '|' in a site name, or an AQI that is not a whole number, must not shift columns or stop a
    # run (a bad file stays in the one-year window): those lines drop out, the rest of the day stands.
    body = fixture("airnow_daily_20260515.dat")
    lines = body.splitlines()
    lines = [
        ln.replace(b"|Compton|", b"|Compton|Annex|") if b"|060371302|" in ln else ln
        for ln in lines
    ]
    lines = [
        ln.replace(b"|61|1|34.3833", b"|6x|1|34.3833")
        if b"|060376012|" in ln and b"|OZONE-8HR|" in ln
        else ln
        for ln in lines
    ]
    days = airnow.read_daily(bytes([10]).join(lines), "20260515")
    la = days.filter(pl.col("aqsid").str.starts_with("06037"))
    assert "060371302" not in set(la["aqsid"])
    assert la.filter(pl.col("aqsid") == "060376012")["aqi"].to_list() == [
        52
    ]  # its PM2.5 row survives
    assert (
        len(la)
        == len(
            airnow.read_daily(body, "20260515").filter(
                pl.col("aqsid").str.starts_with("06037")
            )
        )
        - 4
    )


def test_airnow_tie_breaks_are_deterministic():
    # A tie, made from a real row copied under a higher (unused) LA site id: the lower site id wins.
    body = fixture("airnow_daily_20260515.dat")
    line = next(
        ln for ln in body.split(b"\n") if b"|060376012|" in ln and b"OZONE-8HR" in ln
    )
    twin = line.replace(b"|060376012|", b"|060379033|").replace(
        b"|840060376012", b"|840060379033"
    )
    raw = {
        "airnow-20260515": body + twin + b"\n",
        "airnow-sites": fixture("Monitoring_Site_Locations_V2.dat"),
    }
    la = airnow.county_days(raw).filter(pl.col("fips") == "06037").row(0, named=True)
    assert (
        la["aqi"] == 61
        and la["defining_site"] == "06-037-6012"
        and la["sites_reporting"] == 9
    )


# --- the stitch -------------------------------------------------------------------------------------


def test_preliminary_rows_extend_the_series_after_validated_data(table):
    la = rows(table, "us-county-06037")
    assert la["2026-05-15"] == {
        "series": "aqi",
        "location": "us-county-06037",
        "date": "2026-05-15",
        "aqi": 61,
        "category": "Moderate",
        "defining_parameter": "Ozone",
        "defining_site": "06-037-6012",
        "sites_reporting": 8,
        "validated": False,
    }
    # Kittitas and Catano: validated through Feb/Mar 2026, AirNow fills the later days
    assert rows(table, "us-county-53037")["2026-04-15"]["validated"] is False
    assert rows(table, "us-county-72033")["2026-05-15"]["aqi"] == 36


def test_validated_rows_always_win(table):
    la = rows(table, "us-county-06037")
    # AirData has LA on 2025-12-15 (93, PM2.5); AirNow's 70 for that day never replaces it
    assert la["2025-12-15"]["aqi"] == 93 and la["2025-12-15"]["validated"] is True
    assert table.select(["location", "date"]).is_duplicated().sum() == 0


def test_no_preliminary_row_inside_the_validated_record(table):
    # LA is validated through 2026-04-30, so AirNow's 2026-04-15 value is not used even though AirData
    # has no LA row for that day in the fixture: a gap in validated data is not filled with raw data.
    assert "2026-04-15" not in rows(table, "us-county-06037")
    # AirData's 2025 is a complete year: no AirNow value from 2025 survives for any county
    prelim = table.filter(~pl.col("validated"))
    assert prelim["date"].min() > "2025-12-31"
    assert "2025-12-15" not in rows(
        table, "us-county-45091"
    )  # York County, SC: no validated 2025 row at all


def test_stitch_keeps_preliminary_rows_when_the_latest_airdata_year_is_older():
    # With only the 1980-2025 files, AirData's latest year is 2025, so 2025 is not yet complete and
    # AirNow can fill days after each county's last validated date.
    table = county_aqi_daily.transform(daily_raw(years=(1980, 2000, 2025)))
    york = rows(table, "us-county-45091")
    assert york["2025-12-15"]["validated"] is False and york["2025-12-15"]["aqi"] == 17
    la = rows(table, "us-county-06037")
    assert la["2025-12-15"]["validated"] is True and la["2025-12-15"]["aqi"] == 93
    assert la["2026-04-15"]["validated"] is False


# --- resources and end-to-end -------------------------------------------------------------------------


def test_resources_follow_the_page_and_the_listing(monkeypatch):
    fake = FakeGet().install(monkeypatch)
    res = county_aqi_daily.resources(
        CollectContext(load_contract("airdata/county-aqi-daily"), NOW)
    )
    assert res["airdata-1980"] == f"{AIRDATA}/daily_aqi_by_county_1980.zip"
    assert res["airdata-2026"] == f"{AIRDATA}/daily_aqi_by_county_2026.zip"
    assert sum(k.startswith("airdata-") for k in res) == 47
    days = sorted(k for k in res if k.startswith("airnow-2"))
    # a year of days, ending yesterday (today's file is still filling up)
    assert (
        days[0] == "airnow-20251023"
        and days[-1] == "airnow-20261023"
        and len(days) == 366
    )
    assert (
        res["airnow-20261023"]
        == "https://files.airnowtech.org/airnow/2026/20261023/daily_data_v2.dat"
    )
    assert (
        res["airnow-sites"]
        == "https://files.airnowtech.org/airnow/2026/20261001/Monitoring_Site_Locations_V2.dat"
    )
    assert [p["prefix"] for _u, p in fake.calls if p] == [
        "airnow/2025/", "airnow/2026/",  # the day folders
        "airnow/2026/20261021/", "airnow/2026/20261022/", "airnow/2026/20261023/",  # the newest days' files
        "airnow/2026/20261001/",  # the month's locations file
    ]  # fmt: skip


def test_resources_skip_days_missing_from_the_listing(monkeypatch):
    fake = FakeGet().install(monkeypatch)
    listing = fixture("airnow_listing_2026.xml").replace(
        b"<Prefix>airnow/2026/20260704/</Prefix>", b""
    )
    monkeypatch.setattr(
        airnow,
        "listed_days",
        lambda ctx, years: {
            d
            for y in years
            for d in _days(
                listing if y == 2026 else fixture(f"airnow_listing_{y}.xml"), y
            )
        },
    )
    # yesterday's folder exists but its daily file has not been written yet
    folder = fixture("airnow_folder_20261023.xml")
    fake.folders["20261023"] = re.sub(
        rb"<Contents><Key>airnow/2026/20261023/daily_data_v2.dat</Key>.*?</Contents>",
        b"",
        folder,
    )
    assert fake.folders["20261023"] != folder
    res = county_aqi_daily.resources(
        CollectContext(load_contract("airdata/county-aqi-daily"), NOW)
    )
    assert "airnow-20260703" in res and "airnow-20260704" not in res
    assert "airnow-20261022" in res and "airnow-20261023" not in res


def _days(body: bytes, year: int) -> set[str]:
    return {
        m.decode()
        for m in re.findall(
            rf"<Prefix>airnow/{year}/({year}\d{{4}})/</Prefix>".encode(), body
        )
    }


def _bodies() -> dict[str, bytes]:
    bodies = {
        f"{AIRDATA}/daily_aqi_by_county_{y}.zip": airdata_zip(
            f"daily_aqi_by_county_{y}.csv"
        )
        for y in DAILY_YEARS
    }
    for d in AIRNOW_DAYS:
        bodies[f"https://files.airnowtech.org/airnow/{d[:4]}/{d}/daily_data_v2.dat"] = (
            fixture(f"airnow_daily_{d}.dat")
        )
    # Only the fixture days are "listed", so the locations file falls back to the latest of them; the fake
    # serves the 2026-10-01 capture there.
    bodies[
        "https://files.airnowtech.org/airnow/2026/20260515/Monitoring_Site_Locations_V2.dat"
    ] = fixture("Monitoring_Site_Locations_V2.dat")
    return bodies


def _run(store, upstream, monkeypatch, now=NOW):
    """The page links 1980-2026; serve the fixture years only, and the AirNow days we have."""
    FakeGet().install(monkeypatch)
    monkeypatch.setattr(
        county_aqi_daily, "airdata_years", lambda page, prefix: list(DAILY_YEARS)
    )
    monkeypatch.setattr(airnow, "listed_days", lambda ctx, years: set(AIRNOW_DAYS))
    monkeypatch.setattr(
        airnow,
        "folder_files",
        lambda ctx, day: {"daily_data_v2.dat", airnow.SITES_FILE},
    )
    return run_dataset(daily_contract(), store, fetcher=upstream, now=now)


def test_end_to_end_publishes_then_is_unchanged(store, monkeypatch):
    upstream = Upstream(_bodies())
    first = _run(store, upstream, monkeypatch)
    assert first.outcome == "published", first.report
    published = read_table(store, _download_blob(store))
    assert published.filter(pl.col("location") == "us-county-06037").height == len(
        rows(county_aqi_daily.transform(daily_raw()), "us-county-06037")
    )

    upstream.requests.clear()
    again = _run(store, upstream, monkeypatch)
    assert again.outcome == "unchanged"
    assert all(
        etag for _url, etag in upstream.requests
    )  # every request was conditional


def test_validation_blocks_a_truncated_airdata_reissue(store, monkeypatch):
    upstream = Upstream(_bodies())
    assert _run(store, upstream, monkeypatch).outcome == "published"
    # EPA reissues the 2025 file cut off after its header: the rows vanish and the row-count floor blocks it
    header = fixture("daily_aqi_by_county_2025.csv").split(b"\n")[0] + b"\n"
    upstream.bodies[f"{AIRDATA}/daily_aqi_by_county_2025.zip"] = airdata_zip(
        "daily_aqi_by_county_2025.csv", header
    )
    result = _run(store, upstream, monkeypatch)
    assert result.outcome == "blocked"
    assert any("row count fell" in e for e in result.report.errors)


def test_validation_blocks_an_unknown_county_code(store, monkeypatch):
    upstream = Upstream(_bodies())
    body = fixture("daily_aqi_by_county_2026.csv").replace(
        b'"25","007","2026-02-28"', b'"25","099","2026-02-28"'
    )
    upstream.bodies[f"{AIRDATA}/daily_aqi_by_county_2026.zip"] = airdata_zip(
        "daily_aqi_by_county_2026.csv", body
    )
    result = _run(store, upstream, monkeypatch)
    assert result.outcome == "blocked"
    assert any("us-county-25099" in e for e in result.report.errors)


def _download_blob(store) -> str:
    import json

    manifest = json.loads(
        store.get("sites/airdata/datasets/county-aqi-daily/manifest.json")[0]
    )
    doc = json.loads(
        store.get(
            f"sites/airdata/datasets/county-aqi-daily/versions/{manifest['current']}.json"
        )[0]
    )
    return doc["files"]["download/county-aqi-daily.parquet"]["blob"]
