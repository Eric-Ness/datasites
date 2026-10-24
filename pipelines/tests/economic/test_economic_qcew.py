from datetime import date

import polars as pl
import pytest

from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_economic import bls_qcew
from economic_helpers import FakeContext, FakeUpstream, at, contract, fixture

SLICES = {"2023-q4": "qcew_2023_q4_industry_10.csv", "2026-q1": "qcew_2026_q1_industry_10.csv"}
# The excerpts hold 18 rows, one of them withheld (Eureka County NV), so tests that validate them relax
# the row floor and null limits that guard the full panel.
SMALL = {"min_rows": None, "max_null_fraction": {}}


@pytest.fixture
def raw():
    return {name: fixture(path) for name, path in SLICES.items()}


@pytest.fixture
def table(raw):
    return bls_qcew.transform(raw)


def row(table, location, date):
    found = table.filter((pl.col("location") == location) & (pl.col("date") == date))
    assert len(found) == 1, f"{location}/{date}: {len(found)} rows"
    return found.row(0, named=True)


def test_keeps_total_covered_for_nation_states_and_counties(table):
    assert set(table.filter(pl.col("date") == "2026-01")["location"]) == {
        "usa", "us-de", "us-county-10001", "us-county-10003", "us-county-10005",
        "us-county-09110", "us-pr", "us-county-72001", "us-vi", "us-county-78010",
    }  # fmt: skip
    # Dropped: ownership breakdowns (own_code 1, 5), the unknown-county row 10999, the Dover MSA (C2010),
    # a combined statistical area (CS428) and the national MSA aggregate (USMSA).
    assert set(table["series"]) == {"total-covered"}


def test_values_and_quarter_dates(table):
    assert row(table, "us-county-10001", "2026-01") == {
        "series": "total-covered",
        "location": "us-county-10001",
        "date": "2026-01",
        "establishments": 6115,
        "month1_employment": 69107,
        "month2_employment": 68236,
        "month3_employment": 68769,
        "total_wages": 1054338778,
        "avg_weekly_wage": 1180,
        "suppressed": False,
    }
    assert row(table, "usa", "2023-10")["month3_employment"] == 155014465  # Q4 starts in October


def test_unknown_county_row_is_why_counties_fall_short_of_the_state(table):
    counties = table.filter(pl.col("location").str.starts_with("us-county-10") & (pl.col("date") == "2026-01"))
    state = row(table, "us-de", "2026-01")["month1_employment"]
    assert counties["month1_employment"].sum() + 8753 == state  # 8,753 jobs sit in "Unknown Or Undefined" (10999)


def test_withheld_values_become_null_but_establishments_stay(table):
    eureka = row(table, "us-county-32011", "2023-10")
    assert eureka["suppressed"] is True
    assert eureka["establishments"] == 50
    assert [eureka[k] for k in ("month1_employment", "month3_employment", "total_wages", "avg_weekly_wage")] == [None] * 4
    assert row(table, "us-nv", "2023-10")["month1_employment"] == 1550654


def test_connecticut_counties_before_2024_planning_regions_after(table):
    assert row(table, "us-county-09001", "2023-10")["month1_employment"] == 419407
    assert row(table, "us-county-09110", "2026-01")["month1_employment"] == 526924
    assert table.filter(pl.col("location").str.starts_with("us-county-09") & (pl.col("date") == "2026-01"))["location"].to_list() == [
        "us-county-09110"
    ]


def test_every_location_is_allowed_by_the_contract(table):
    # Legacy CT county and the Virgin Islands districts are explicit in the contract; the rest are registry ids.
    report = validate(contract("qcew", **SMALL), table, None)
    assert report.errors == []


def test_latest_quarter_steps_back_until_a_slice_exists():
    seen = []

    def published(url):
        seen.append(url)
        return url.endswith("/2026/1/industry/10.csv")

    assert bls_qcew.latest_quarter(date(2026, 10, 24), published) == (2026, 1)
    # Q4 and Q3 2026 can't be out yet, so the first probe is Q2 2026.
    assert seen == [bls_qcew.URL.format(year=2026, quarter=2), bls_qcew.URL.format(year=2026, quarter=1)]


def test_latest_quarter_gives_up_after_two_years():
    with pytest.raises(RuntimeError, match="no QCEW industry slice"):
        bls_qcew.latest_quarter(date(2026, 10, 24), lambda url: False)


def test_resources_cover_2014_through_the_latest_quarter(monkeypatch):
    monkeypatch.setattr(bls_qcew, "latest_quarter", lambda today: (2026, 1))
    urls = bls_qcew.resources(FakeContext(contract("qcew"), responses=None))
    assert len(urls) == 12 * 4 + 1
    assert urls["2014-q1"] == "https://data.bls.gov/cew/data/api/2014/1/industry/10.csv"
    assert list(urls)[-1] == "2026-q1"


def test_a_slice_for_the_wrong_quarter_is_rejected(raw):
    with pytest.raises(ValueError, match="2024-q1 does not hold 2024 Q1"):
        bls_qcew.transform({"2024-q1": raw["2026-q1"]})


def test_an_error_page_instead_of_a_slice_is_rejected():
    with pytest.raises(ValueError, match="missing columns"):
        bls_qcew.transform({"2026-q1": b"<html><body>Access Denied</body></html>\n"})


def test_end_to_end_publish(store, monkeypatch):
    # Only two quarters were captured, so resources() is narrowed to them.
    monkeypatch.setattr(
        bls_qcew, "resources", lambda ctx: {name: bls_qcew.URL.format(year=name[:4], quarter=name[-1]) for name in SLICES}
    )
    fake = FakeUpstream({bls_qcew.URL.format(year=name[:4], quarter=name[-1]): fixture(path) for name, path in SLICES.items()})
    result = run_dataset(contract("qcew", **SMALL), store, fetcher=fake, now=at(24))
    assert result.outcome == "published", result.report
    doc = get_json(store, f"sites/economicdata/datasets/qcew/versions/{result.version}.json")[0]
    assert doc["coverage"] == {"start": "2023-10", "end": "2026-01"}
    assert doc["locations"]["usa"] == "United States (50 states and DC)"
    assert doc["locations"]["us-county-09001"] == "Fairfield County, CT (legacy county, through 2023)"
    assert doc["locations"]["us-county-78010"] == "St. Croix, VI"
    assert "total-covered/us-county-10001.json" in doc["files"]


def test_validation_blocks_when_the_newest_quarter_disappears(store, monkeypatch):
    urls = {name: bls_qcew.URL.format(year=name[:4], quarter=name[-1]) for name in SLICES}
    fake = FakeUpstream({urls[name]: fixture(path) for name, path in SLICES.items()})
    monkeypatch.setattr(bls_qcew, "resources", lambda ctx: urls)
    c = contract("qcew", **SMALL)
    assert run_dataset(c, store, fetcher=fake, now=at(24)).outcome == "published"
    # A later run that only finds the older slice (say BLS pulled 2026 Q1 for a correction) must not
    # publish a dataset whose newest quarter went backwards.
    monkeypatch.setattr(bls_qcew, "resources", lambda ctx: {"2023-q4": urls["2023-q4"]})
    result = run_dataset(c, store, fetcher=fake, now=at(25))
    assert result.outcome == "blocked"
    assert any("newest date went from 2026-01 to 2023-10" in e for e in result.report.errors)
