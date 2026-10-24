"""Washington adapter, tested against the Secretary of State's certified 2024 (and 2022) All Counties exports."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from election_helpers import crlf, fixture_bytes

from datasites_core.contracts import parse_contract
from datasites_core.validate import validate_table
from datasites_election import certified_2026
from datasites_election.adapters import ADAPTERS, to_county_rows
from datasites_election.adapters.wa import Washington, classify

WA = ADAPTERS["WA"]
FIXTURE_2024 = "wa_20241105_allcounties_excerpt.csv"
FIXTURE_2022 = "wa_20221108_allcounties_excerpt.csv"
CERTIFIED_2024 = certified_2026.Release(
    "WA", election_date="2024-11-05", certified_at="2024-12-04", certification_url="https://www.sos.wa.gov/node/13114"
)
URL_2024 = "https://results.vote.wa.gov/results/20241105/export/20241105_AllCounties.csv"


def build(body: bytes, release=CERTIFIED_2024) -> pl.DataFrame:
    held = date.fromisoformat(release.election_date)
    return certified_2026.build(WA, {"AllCounties": body}, release, WA.resources(held))


@pytest.fixture(scope="module")
def table():
    return build(fixture_bytes(FIXTURE_2024))


def statewide(table, office):
    return dict(
        table.filter((pl.col("office") == office) & (pl.col("mode") == "TOTAL"))
        .group_by("candidate")
        .agg(pl.col("candidatevotes").sum())
        .iter_rows()
    )


def test_urls_follow_the_election_date():
    assert WA.resources(date(2024, 11, 5)) == {"AllCounties": URL_2024}
    assert WA.resources(date(2026, 11, 3)) == {
        "AllCounties": "https://results.vote.wa.gov/results/20261103/export/20261103_AllCounties.csv"
    }


def test_output_matches_the_generated_contract(table):
    contract = parse_contract("electiondata", certified_2026.contract("WA"), "generated")
    assert table.columns == contract.columns
    assert validate_table(contract, table, None).status == "pass"


def test_statewide_totals_match_the_certified_results(table):
    # Washington's certified 2024 results; MEDSL's Senate file has the same Senate figures.
    assert statewide(table, "US SENATE") == {"MARIA CANTWELL": 2252577, "DR RAUL GARCIA": 1549187, "WRITE-IN": 10627}
    president = statewide(table, "US PRESIDENT")
    assert president["KAMALA D. HARRIS / TIM WALZ"] == 2245849
    assert president["DONALD J. TRUMP / JD VANCE"] == 1530923
    assert president["WRITE-IN"] == 25408
    assert table.filter(pl.col("office") == "US PRESIDENT")["county_name"].n_unique() == 39


def test_king_county_rows(table):
    king = table.filter((pl.col("county_name") == "KING") & (pl.col("office") == "US SENATE")).sort("candidatevotes", descending=True)
    assert king.select("candidate", "party_detailed", "party", "writein", "candidatevotes", "totalvotes").rows() == [
        ("MARIA CANTWELL", "(PREFERS DEMOCRATIC PARTY)", "DEMOCRAT", False, 828204, 1103406),
        ("DR RAUL GARCIA", "(PREFERS GOP PARTY)", "REPUBLICAN", False, 271832, 1103406),
        ("WRITE-IN", "", "OTHER", True, 3370, 1103406),
    ]
    row = king.row(0, named=True)
    assert (row["county_fips"], row["county_id"], row["state"], row["year"], row["mode"]) == (
        "53033", "us-county-53033", "WASHINGTON", "2024", "TOTAL",
    )  # fmt: skip
    assert (row["status"], row["certified_at"], row["source_url"]) == ("certified", "2024-12-04", URL_2024)


def test_totalvotes_gives_the_states_own_percentages(table):
    """totalvotes includes write-ins, which is how Washington computes PercentageOfTotalVotes."""
    source = pl.read_csv(fixture_bytes(FIXTURE_2024), infer_schema_length=0)
    pct = {(r["County"].upper(), r["Candidate"].upper(), r["Race"]): float(r["PercentageOfTotalVotes"]) for r in source.iter_rows(named=True)}
    race_of = {"US SENATE": "United States U.S. Senator", "US PRESIDENT": "United States President/Vice President"}
    checked = 0
    for r in table.filter(pl.col("office").is_in(list(race_of))).iter_rows(named=True):
        share = 100 * r["candidatevotes"] / r["totalvotes"]
        assert share == pytest.approx(pct[(r["county_name"], r["candidate"], race_of[r["office"]])], abs=0.0001)
        checked += 1
    assert checked == 429 + 117


def test_contests_kept_and_skipped(table):
    offices = set(table.select("office", "district").unique().iter_rows())
    assert offices == {
        ("US PRESIDENT", "STATEWIDE"),
        ("US SENATE", "STATEWIDE"),
        ("SUPERINTENDENT OF PUBLIC INSTRUCTION", "STATEWIDE"),
        ("STATE SUPREME COURT JUSTICE POSITION 8", "STATEWIDE"),
        *(("US HOUSE", f"{n:03d}") for n in range(1, 11)),
    }
    # The initiative, the legislative, county and city contests in the fixture are not federal or statewide offices.
    assert classify("State Executive", "Washington State Initiative Measure No. 2066") is None
    assert classify("Legislative", "LEGISLATIVE DISTRICT 1 State Senator") is None
    assert classify("County", "Adams County Commissioner District 2") is None
    # Washington writes the same contest name in two cases.
    assert classify("Congressional", "CONGRESSIONAL DISTRICT 10 U.S. Representative") == ("US HOUSE", "010")
    assert classify("Congressional", "Congressional District 1 U.S. Representative") == ("US HOUSE", "001")
    house = table.filter((pl.col("office") == "US HOUSE") & (pl.col("district") == "001"))
    assert sorted(house["county_name"].unique().to_list()) == ["KING", "SNOHOMISH"]


def test_parties(table):
    supt = table.filter(pl.col("office") == "SUPERINTENDENT OF PUBLIC INSTRUCTION")
    assert set(supt.filter(~pl.col("writein"))["party"]) == {"NONPARTISAN"}
    assert set(supt.filter(pl.col("writein"))["party"]) == {"OTHER"}
    pres = dict(table.filter(pl.col("office") == "US PRESIDENT").select("candidate", "party").unique().iter_rows())
    assert pres["KAMALA D. HARRIS / TIM WALZ"] == "DEMOCRAT"
    assert pres["CHASE OLIVER / MIKE TER MAAT"] == "LIBERTARIAN"
    assert pres["JILL STEIN / RUDOLPH WARE"] == "GREEN"
    assert pres["SHIVA AYYADURAI / CRYSTAL ELLIS"] == "OTHER"  # "Independent Candidates"
    assert pres["ROBERT F. KENNEDY JR. / NICOLE SHANAHAN"] == "OTHER"  # "We The People Party Nominees"


def test_live_crlf_line_endings_parse_the_same(table):
    assert build(crlf(fixture_bytes(FIXTURE_2024))).equals(table)


def test_unofficial_until_certified():
    unofficial = build(fixture_bytes(FIXTURE_2024), certified_2026.Release("WA", election_date="2024-11-05"))
    assert set(unofficial["status"]) == {"unofficial"}
    assert unofficial["certified_at"].null_count() == len(unofficial)


def test_unrecognized_federal_contest_stops_the_run():
    body = fixture_bytes(FIXTURE_2024).replace(b'"United States U.S. Senator"', b'"United States U.S. Senator Unexpired Term"', 3)
    with pytest.raises(ValueError, match="unrecognized federal contest"):
        build(body)
    body = fixture_bytes(FIXTURE_2024) + b'"Adams","Washington State Lands Commissioner","A","","1","100","State Executive"\n'
    with pytest.raises(ValueError, match="unrecognized statewide contest"):
        build(body)


def test_a_file_missing_a_county_is_rejected():
    lines = fixture_bytes(FIXTURE_2024).split(b"\n")
    body = b"\n".join(line for line in lines if not line.startswith(b'"Wahkiakum",'))
    with pytest.raises(ValueError, match=r"US PRESIDENT has no results for counties \['53069'\]"):
        build(body)


def test_special_election_flag_2022():
    release = certified_2026.Release("WA", election_date="2022-11-08", special=(("SECRETARY OF STATE", "STATEWIDE"),))
    table = build(fixture_bytes(FIXTURE_2022), release)
    flags = dict(table.select("office", "special").unique().iter_rows())
    assert flags == {"SECRETARY OF STATE": True, "US SENATE": False}
    sos = statewide(table, "SECRETARY OF STATE")
    assert sos == {"STEVE HOBBS": 1468521, "JULIE ANDERSON": 1351926, "WRITE-IN": 129933}
    assert table.filter((pl.col("office") == "SECRETARY OF STATE") & (pl.col("candidate") == "JULIE ANDERSON"))["party"].unique().to_list() == ["OTHER"]
    wrong = certified_2026.Release("WA", election_date="2022-11-08", special=(("GOVERNOR", "STATEWIDE"),))
    with pytest.raises(ValueError, match="special elections"):
        build(fixture_bytes(FIXTURE_2022), wrong)


def test_rows_without_counties_need_an_adapter_that_says_so():
    """Alaska-style files report by units that aren't counties; the check for county codes depends on the adapter."""

    class ByDistrict(Washington):
        reports_by_county = False

        def parse(self, raw):
            return super().parse(raw).with_columns(pl.lit(None, dtype=pl.Utf8).alias("county_fips"))

    rows = to_county_rows(ByDistrict(), {"AllCounties": fixture_bytes(FIXTURE_2024)}, 2024, stage="GEN", special=set())
    assert rows["county_fips"].null_count() == rows["county_id"].null_count() == len(rows)

    class Careless(ByDistrict):
        reports_by_county = True

    with pytest.raises(ValueError, match="rows without a county FIPS code"):
        to_county_rows(Careless(), {"AllCounties": fixture_bytes(FIXTURE_2024)}, 2024, stage="GEN", special=set())
