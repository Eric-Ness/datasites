"""Washington adapter, tested against the Secretary of State's certified 2024 (and 2022) All Counties exports."""

from __future__ import annotations

import json
from datetime import date

import fastexcel
import polars as pl
import pytest
from election_helpers import FIXTURES, crlf, fixture_bytes

from datasites_core.contracts import parse_contract
from datasites_core.validate import validate_table
from datasites_election import certified_2026
from datasites_election.adapters import ADAPTERS, to_county_rows
from datasites_election.adapters.wa import Washington, classify, classify_office, report_url

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
    assert WA.resources(date(2022, 11, 8)) == {
        "AllCounties": "https://results.vote.wa.gov/results/20221108/export/20221108_AllCounties.csv"
    }
    # 2026 moved to results.votewa.gov, where the file name is looked up (test_2026_locates_the_report_through_the_api).


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


# --- 2026 on: the results.votewa.gov "All Results" XLSX ---------------------------------------------

FIXTURE_2026 = "wa_20261103_all_results.xlsx"  # the whole report, as downloaded on 2026-11-26 (unofficial)
API_2026 = {
    "electionDate": "2026-11-03",
    "jurisdictionId": "01000000-f977-1a83-1ed4-08ddd69e9e69",
    "publicReportCategories": [
        {
            "reports": [
                {"reportName": "All Results Excel", "blobName": "All Results_aac7876f-8115-4dd0-bea7-22f1d65b32bf.xlsx"},
                {"reportName": "Summary and Precinct Results Excel", "blobName": "Results_a64121e6.xlsx"},
            ]
        }
    ],
}
URL_2026 = (
    "https://results.votewa.gov/cdn/results/01000000-f977-1a83-1ed4-08ddd69e9e69/"
    "All%20Results_aac7876f-8115-4dd0-bea7-22f1d65b32bf.xlsx"
)


@pytest.fixture(scope="module")
def body_2026() -> bytes:
    return (FIXTURES / FIXTURE_2026).read_bytes()


@pytest.fixture(scope="module")
def table_2026(body_2026):
    return certified_2026.build(WA, {"AllResults": body_2026}, certified_2026.RELEASES["WA"], {"AllResults": URL_2026})


def test_2026_locates_the_report_through_the_api():
    asked = []

    def get(url):
        asked.append(url)
        return json.dumps(API_2026).encode()

    assert WA.locate(date(2026, 11, 3), get) == {"AllResults": URL_2026}
    assert asked == ["https://results.votewa.gov/results/public/api/elections/washington/20261103"]
    # Older elections need no lookup and stay on the old site.
    assert WA.locate(date(2024, 11, 5), get) == {"AllCounties": URL_2024} and len(asked) == 1
    with pytest.raises(ValueError, match="use locate"):
        WA.resources(date(2026, 11, 3))


def test_2026_report_lookup_refuses_surprises():
    with pytest.raises(ValueError, match="not 20261103"):
        report_url({**API_2026, "electionDate": "2026-08-04"}, "20261103")
    with pytest.raises(ValueError, match="expected one 'All Results Excel' report"):
        report_url({**API_2026, "publicReportCategories": []}, "20261103")


def test_2026_contests_and_counties(table_2026):
    offices = set(table_2026.select("office", "district").unique().iter_rows())
    assert offices == {("US HOUSE", f"{n:03d}") for n in range(1, 11)} | {
        (f"STATE SUPREME COURT JUSTICE POSITION {n}", "STATEWIDE") for n in (1, 3, 4, 5, 7)
    }
    assert table_2026["county_fips"].n_unique() == 39
    assert set(table_2026["status"].to_list()) == {"unofficial"} and table_2026["certified_at"].null_count() == len(table_2026)
    assert set(table_2026["source_url"].to_list()) == {URL_2026}
    # Ballots Cast / Over Votes / Under Votes lines are not candidates.
    assert not table_2026.filter(pl.col("candidate").is_in(["BALLOTS CAST", "OVER VOTES", "UNDER VOTES"])).height


def test_2026_county_rows_add_up_to_the_states_own_summary_sheet(body_2026, table_2026):
    """Every candidate's county votes sum to the statewide total on the report's "Summary Results" sheet."""
    summary = fastexcel.read_excel(body_2026).load_sheet("Summary Results", dtypes="string").to_polars()
    summary = summary.with_columns(
        pl.col("Office Name").map_elements(lambda n: (classify_office(n) or ("", ""))[0] + "|" + (classify_office(n) or ("", ""))[1], return_dtype=pl.Utf8).alias("key"),
        pl.col("Ballot Name").str.to_uppercase().alias("candidate"),
        pl.col("Total").str.replace(r"\.0+$", "").cast(pl.Int64).alias("votes"),
    ).filter((pl.col("key") != "|") & ~pl.col("candidate").is_in(["BALLOTS CAST", "OVER VOTES", "UNDER VOTES"]))
    ours = table_2026.group_by(pl.concat_str("office", pl.lit("|"), "district").alias("key"), "candidate").agg(pl.col("candidatevotes").sum().alias("votes"))
    joined = summary.select("key", "candidate", "votes").join(ours, on=["key", "candidate"], how="full", suffix="_ours")
    assert joined.height == ours.height == summary.height
    assert joined.filter(pl.col("votes") != pl.col("votes_ours")).height == 0
    cd1 = dict(ours.filter(pl.col("key") == "US HOUSE|001").select("candidate", "votes").iter_rows())
    assert cd1 == {"SUZAN DELBENE": 199779, "MARY SILVA": 95216, "WRITE-IN": 967}


def test_2026_contest_names():
    assert classify_office("Congressional District 10 - U.S. Representative") == ("US HOUSE", "010")
    assert classify_office("Supreme Court - Justice Position #07") == ("STATE SUPREME COURT JUSTICE POSITION 7", "STATEWIDE")
    assert classify_office("U.S. Senator") == ("US SENATE", "STATEWIDE")
    assert classify_office("United States President/Vice President") == ("US PRESIDENT", "STATEWIDE")
    assert classify_office("Washington State Governor") == ("GOVERNOR", "STATEWIDE")
    assert classify_office("Legislative District 6 - State Senator") is None
    assert classify_office("Initiative Measure No. IP26-645") is None
    assert classify_office("City of Bothell - Proposition No. 1") is None
    with pytest.raises(ValueError, match="unrecognized federal or statewide contest"):
        classify_office("U.S. Senator Unexpired Term")
