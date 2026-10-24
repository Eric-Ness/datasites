import polars as pl
import pytest

from datasites_core.contracts import load_contract
from datasites_core.layout import manifest_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_economic import bls_laus
from economic_helpers import FakeUpstream, at, contract, fixture

FILES = {
    "la.area": "la.area.txt",
    "la.series": "la.series.txt",
    "states-nsa": "la.data.2.AllStatesU.txt",
    "states-sa": "la.data.3.AllStatesS.txt",
    "metro": "la.data.60.Metro.txt",
    "micro": "la.data.62.Micro.txt",
    "county": "la.data.64.County.txt",
}


@pytest.fixture
def raw():
    return {name: fixture(path) for name, path in FILES.items()}


@pytest.fixture
def table(raw):
    return bls_laus.transform(raw)


def row(table, location, date, series="nsa"):
    found = table.filter((pl.col("series") == series) & (pl.col("location") == location) & (pl.col("date") == date))
    assert len(found) == 1, f"{series}/{location}/{date}: {len(found)} rows"
    return found.row(0, named=True)


def test_columns_match_the_contract(table):
    c = load_contract("economicdata/laus")
    assert sorted(table.drop("location_name").columns) == sorted(c.columns)
    assert table.schema["labor_force"] == pl.Int64 and table.schema["unemployment_rate"] == pl.Float64


def test_four_measures_become_one_row_per_area_and_month(table):
    assert row(table, "us-county-10001", "2025-09") == {
        "series": "nsa",
        "location": "us-county-10001",
        "date": "2025-09",
        "labor_force": 84982,
        "employed": 80260,
        "unemployed": 4722,
        "unemployment_rate": 5.6,
        "preliminary": False,
        "footnotes": None,
        "location_name": "Kent County, DE",
    }


def test_other_measures_and_annual_averages_are_left_out(table):
    # la.data.2 also carries measure 07 (employment-population ratio); it must not leak into any column.
    de = row(table, "us-de", "2026-09")
    assert (de["labor_force"], de["employed"], de["unemployed"], de["unemployment_rate"]) == (502236, 479206, 23030, 4.6)
    # M13 is the annual average (footnote G for 2025's 11-month average): not a month.
    assert not table["date"].str.ends_with("-13").any()
    assert "G" not in "".join(table["footnotes"].drop_nulls())


def test_preliminary_flag_comes_from_footnote_p(table):
    latest = row(table, "us-county-10001", "2026-08")
    assert latest["preliminary"] is True and latest["footnotes"] == "P"
    assert latest["unemployment_rate"] == 5.5
    before = row(table, "us-county-10001", "2026-07")
    assert before["preliminary"] is False and before["footnotes"] is None


def test_october_2025_is_missing_and_never_interpolated(table):
    for location in ("us-county-10001", "us-de", "us-cbsa-20100", "us-county-09110"):
        gap = row(table, location, "2025-10")
        assert gap["footnotes"] == "X"
        assert gap["preliminary"] is False
        assert [gap[k] for k in ("labor_force", "employed", "unemployed", "unemployment_rate")] == [None] * 4
    assert row(table, "us-de", "2025-10", series="sa")["unemployment_rate"] is None
    # The months around the gap are untouched.
    assert row(table, "us-county-10001", "2025-09")["unemployment_rate"] == 5.6
    assert row(table, "us-county-10001", "2025-11")["unemployment_rate"] == 5.9


def test_puerto_rico_was_surveyed_in_october_2025(table):
    pr = row(table, "us-pr", "2025-10")
    assert (pr["unemployment_rate"], pr["labor_force"], pr["footnotes"]) == (5.7, 1224524, None)
    assert row(table, "us-county-72001", "2025-10")["unemployment_rate"] == 15.7


def test_other_footnote_codes(table):
    # N: not available (Jefferson Parish after Hurricane Katrina; Puerto Rico, April 2020)
    katrina = row(table, "us-county-22051", "2005-09")
    assert katrina["footnotes"] == "N" and katrina["unemployment_rate"] is None
    assert row(table, "us-county-22051", "2005-08")["labor_force"] == 224654
    assert row(table, "us-pr", "2020-04")["footnotes"] == "N"
    # W/V: survey not conducted (Hurricane Maria), interpolated by BLS; Y: controlled to those totals
    assert row(table, "us-pr", "2017-09")["footnotes"] == "W"
    assert row(table, "us-pr", "2017-09")["unemployment_rate"] == 11.0
    assert row(table, "us-pr", "2017-09", series="sa")["footnotes"] == "V"
    assert row(table, "us-county-72001", "2017-09")["footnotes"] == "Y"


def test_series_level_footnote_applies_to_every_month(table):
    # la.series marks St. Louis MSA with A (boundaries differ from OMB's); the data rows carry no A.
    assert row(table, "us-cbsa-41180", "2026-07")["footnotes"] == "A"
    latest = row(table, "us-cbsa-41180", "2026-08")
    assert latest["footnotes"] == "A,P" and latest["preliminary"] is True


def test_cbsa_and_county_codes_stay_apart(table):
    # MT<state FIPS><CBSA>: Dover DE metro is CBSA 20100, not county 10201; Abilene TX is 10180, not
    # Cottle County TX (48101), which is what slicing the area code like a county would give.
    dover = row(table, "us-cbsa-20100", "2026-08")
    kent = row(table, "us-county-10001", "2026-08")
    assert dover["location_name"] == "Dover, DE Metropolitan Statistical Area"
    assert dover["labor_force"] == kent["labor_force"] == 82566  # a one-county metro, two distinct ids
    assert row(table, "us-cbsa-10180", "2026-08")["location_name"] == "Abilene, TX Metropolitan Statistical Area"
    assert row(table, "us-cbsa-42580", "2026-08")["location_name"] == "Seaford, DE Micropolitan Statistical Area"
    locations = set(table["location"])
    assert not locations & {"us-county-10201", "us-county-48101", "us-county-10425"}
    assert {loc for loc in locations if loc.startswith("us-cbsa-")} == {
        "us-cbsa-20100", "us-cbsa-10180", "us-cbsa-41180", "us-cbsa-19820", "us-cbsa-42580",
    }  # fmt: skip


def test_seasonally_adjusted_series(table):
    sa = row(table, "us-de", "2026-09", series="sa")
    assert (sa["labor_force"], sa["unemployment_rate"], sa["preliminary"]) == (504749, 4.6, True)
    assert row(table, "us-de", "2025-09", series="sa")["unemployment_rate"] == 5.2  # NSA was 5.0
    assert row(table, "us-cbsa-19820", "2026-09", series="sa")["labor_force"] == 2175646
    assert set(table.filter(pl.col("series") == "sa")["location"]) == {"us-de", "us-pr", "us-cbsa-19820"}


def test_connecticut_planning_regions_and_legacy_alaska(table):
    capitol = row(table, "us-county-09110", "2026-08")
    assert (capitol["labor_force"], capitol["unemployment_rate"]) == (504866, 5.0)
    valdez = row(table, "us-county-02261", "2019-12")
    assert valdez["unemployment_rate"] == 8.9
    # Every location (registry counties and states, CBSAs, the explicit legacy Alaska area) is allowed.
    report = validate(contract("laus", min_rows=None, max_null_fraction={}), table.drop("location_name"), None)
    assert report.errors == []


def test_crlf_lines_parse_like_lf_lines(raw):
    # The real files mix CRLF and LF line endings (git stores the fixtures as LF).
    crlf = {name: body.replace(b"\n", b"\r\n") for name, body in raw.items()}
    assert bls_laus.transform(crlf).sort("series", "location", "date").equals(bls_laus.transform(raw).sort("series", "location", "date"))


def test_unknown_statewide_area_is_an_error(raw):
    raw["la.area"] += b"A\tST9900000000000\tNowhere\t0\tT\t1\n"
    raw["states-nsa"] += b"LAUST990000000000003          \t2026\tM08\t         4.0\t\n"
    with pytest.raises(ValueError, match="ST9900000000000"):
        bls_laus.transform(raw)


def test_area_missing_from_la_area_is_an_error(raw):
    raw["la.area"] = b"\n".join(line for line in raw["la.area"].split(b"\n") if b"MT4810180000000" not in line)
    with pytest.raises(ValueError, match="MT4810180000000"):
        bls_laus.transform(raw)


def upstream():
    return FakeUpstream({url: fixture(FILES[name]) for name, url in bls_laus.RESOURCES.items()})


# The excerpts are small and gap-heavy (a fifth of their rows are footnoted gaps), so the end-to-end
# runs relax the row floor and null limits that guard the full files.
SMALL = {"min_rows": None, "max_null_fraction": {}}


def test_end_to_end_publish(store):
    c = contract("laus", **SMALL)
    fake = upstream()
    result = run_dataset(c, store, fetcher=fake, now=at(24))
    assert result.outcome == "published", result.report
    doc = get_json(store, f"sites/economicdata/datasets/laus/versions/{result.version}.json")[0]
    assert doc["coverage"] == {"start": "2005-08", "end": "2026-09"}
    assert doc["locations"]["us-county-10001"] == "Kent County"  # registry name
    assert doc["locations"]["us-cbsa-20100"] == "Dover, DE Metropolitan Statistical Area"  # from la.area
    assert doc["locations"]["us-county-02261"] == "Valdez-Cordova Census Area, AK (through 2019)"  # contract
    assert "nsa/us-county-10001.json" in doc["files"] and "sa/us-de.csv" in doc["files"]
    # The 337 MB county file is over raw_archive_max_mb in production; here everything is small and archived.
    assert {r["name"] for r in doc["source"]["raw"]} == set(FILES)

    again = run_dataset(c, store, fetcher=fake, now=at(25))
    assert again.outcome == "unchanged"
    assert all(etag for _, etag in fake.requests[-len(FILES):])  # conditional GETs


def test_validation_blocks_a_truncated_download(store):
    # A county file cut off mid-transfer still parses; the row floor keeps it from replacing good data.
    result = run_dataset(contract("laus"), store, fetcher=upstream(), now=at(24))
    assert result.outcome == "blocked"
    assert any("the contract requires at least 1500000" in e for e in result.report.errors)
    assert get_json(store, manifest_key("economicdata", "laus"))[0] is None


def test_validation_blocks_an_impossible_rate(store):
    fake = upstream()
    fake.replace(bls_laus.RESOURCES["county"], b"2026\tM07\t         5.4", b"2026\tM07\t       154.0")
    result = run_dataset(contract("laus", **SMALL), store, fetcher=fake, now=at(24))
    assert result.outcome == "blocked"
    assert any(e.startswith("unemployment_rate: 1 values outside [0.0, 100.0]") for e in result.report.errors)
