"""housingdata/building-permits: Census BPS annual county files (fixtures: verbatim lines of 1990, 2014, 2025)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import polars as pl
import pytest
from housing_helpers import bps

from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import version_key
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_housing import census_bps

NOW = datetime(2026, 11, 6, 15, 37, tzinfo=timezone.utc)
YEARS = (1990, 2014, 2025)


@pytest.fixture
def contract():
    return load_contract("housingdata/building-permits")


@pytest.fixture
def table():
    return census_bps.transform({f"bps-{y}": bps(y) for y in YEARS})


def at(table, location, year):
    rows = table.filter((pl.col("location") == location) & (pl.col("date") == str(year)))
    assert len(rows) == 1, (location, year)
    return rows.row(0, named=True)


def for_excerpt(contract):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=10))


def test_shape_and_validation(contract, table):
    assert table.columns == ["series", "location", "date", "units", "units_1", "units_2", "units_3_4", "units_5_plus", "buildings", "value"]
    assert validate(for_excerpt(contract), table, None).status == "pass"


def test_figures_from_the_padded_1990s_layout(table):
    # "9099  ,     01  , 001  , 3  , 6  , Autauga County  , 87  , 87  , 4491772  , 0 ..."
    autauga = at(table, "us-county-01001", 1990)
    assert (autauga["units"], autauga["units_1"], autauga["buildings"], autauga["value"]) == (87, 87, 87, 4491772)
    # Former counties keep their own codes.
    assert at(table, "us-county-12025", 1990)["units"] > 0  # Dade County, FL
    assert at(table, "us-county-09001", 1990)["units"] > 0  # Fairfield County, CT
    assert at(table, "us-county-51515", 1990)["location"] == "us-county-51515"  # Bedford city, VA


def test_unit_totals_add_up(table):
    la = at(table, "us-county-06037", 2025)
    assert la["units"] == la["units_1"] + la["units_2"] + la["units_3_4"] + la["units_5_plus"]
    assert la["units_5_plus"] > la["units_1"]  # most new units in Los Angeles County are apartments


def test_balance_of_state_counts_in_the_state_total_only(table):
    assert table.filter(pl.col("location").is_in(["us-county-23000", "us-county-26999"])).is_empty()
    maine = table.filter(pl.col("location").str.starts_with("us-county-23") & (pl.col("date") == "2025"))
    unorganized = 114  # "2025,23,000,1,1,Maine Unorganized Territory ,114,114,..."
    assert at(table, "us-me", 2025)["units"] == maine["units"].sum() + unorganized


def test_a_county_listed_twice_counts_once(table):
    # 2014 lists St. Mary's County, MD and Anchorage, AK twice under two spellings, with the same figures.
    st_marys = at(table, "us-county-24037", 2014)
    assert (st_marys["units_1"], st_marys["units_5_plus"], st_marys["units"]) == (451, 6, 457)
    assert at(table, "us-county-02020", 2014)["units"] == 572 + 22 + 176
    assert at(table, "us-md", 2014)["units"] == 457  # counted once in the state total


def test_national_total_sums_every_row(table):
    rows = table.filter(pl.col("date") == "2025")
    states = rows.filter(pl.col("location").str.contains(r"^us-[a-z]{2}$"))
    assert at(table, "usa", 2025)["units"] == states["units"].sum()


def test_changed_files_stop_the_run():
    good = bps(2025).decode("latin-1")
    with pytest.raises(ValueError, match="unexpected header"):
        census_bps.transform({"bps-2025": good.replace("Date,State,County", "Year,State,County", 1).encode("latin-1")})
    with pytest.raises(ValueError, match="survey date"):
        census_bps.transform({"bps-2024": bps(2025)})
    with pytest.raises(ValueError, match="non-numeric"):
        census_bps.transform({"bps-2025": good.replace(",200,200,82131895,", ",200,n/a,82131895,", 1).encode("latin-1")})
    twice = good.replace("2025,01,001,3,6,Autauga County", "2025,01,001,3,6,Autauga County,1,1,1,0,0,0,0,0,0,0,0,0,1,1,1,0,0,0,0,0,0,0,0,0\n2025,01,001,3,6,Autauga County", 1)
    with pytest.raises(ValueError, match="listed twice with different figures"):
        census_bps.transform({"bps-2025": twice.encode("latin-1")})


def test_resources_lists_every_year_from_the_directory(contract):
    listing = "".join(f"<a>co{y}a.txt</a> <a>co{y}c.txt</a>" for y in (*range(1990, 2026),))
    ctx = type("Ctx", (), {"get": lambda self, url, **_: listing.encode()})()
    res = census_bps.resources(ctx)
    assert len(res) == 36 and res["bps-1990"] == f"{census_bps.BPS}/co1990a.txt" and res["bps-2025"].endswith("co2025a.txt")
    gap = listing.replace("co2001a.txt", "")
    with pytest.raises(ValueError, match="missing for \\[2001\\]"):
        census_bps.resources(type("Ctx", (), {"get": lambda self, url, **_: gap.encode()})())


def test_end_to_end_publish(contract, store, monkeypatch):
    files = {f"{census_bps.BPS}/co{y}a.txt": bps(y) for y in YEARS}
    monkeypatch.setattr(census_bps, "resources", lambda ctx: {f"bps-{y}": f"{census_bps.BPS}/co{y}a.txt" for y in YEARS})
    requests = []

    def fetcher(url, etag, last_modified):
        requests.append((url, last_modified))
        if last_modified:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, files[url], None, "Thu, 14 May 2026 13:45:24 GMT")

    first = publish.run_dataset(for_excerpt(contract), store, fetcher=fetcher, now=NOW)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("housingdata", "building-permits", first.version))
    assert doc["coverage"] == {"start": "1990", "end": "2025"}
    assert {"permits/usa.json", "permits/us-me.json", "permits/us-county-06037.json"} <= set(doc["files"])
    # A week later every file answers 304 to a conditional GET, and nothing is republished.
    second = publish.run_dataset(for_excerpt(contract), store, fetcher=fetcher, now=NOW.replace(day=13))
    assert second.outcome == "unchanged"
    assert all(lm == "Thu, 14 May 2026 13:45:24 GMT" for _, lm in requests[-3:])
