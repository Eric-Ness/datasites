"""Maryland adapter, tested against the State Board of Elections' official 2024 (and 2022) county results files."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from election_helpers import md_files

from datasites_core.contracts import parse_contract
from datasites_core.validate import validate_table
from datasites_election import certified_2026
from datasites_election.adapters import ADAPTERS
from datasites_election.adapters.md import COUNTIES, election_code, trim_line_ends

MD = ADAPTERS["MD"]
FIXTURE_2024 = "md_pg24_county_results_excerpt.txt"
FIXTURE_2022 = "md_gg22_county_results_excerpt.txt"
# The Board of State Canvassers certified the 2024 general election on December 5, 2024 (as reported by the
# Maryland Association of Counties; the Board's results pages say "Official" with that date).
CERTIFIED_2024 = certified_2026.Release(
    "MD",
    election_date="2024-11-05",
    certified_at="2024-12-05",
    certification_url="https://conduitstreet.mdcounties.org/2024/12/05/maryland-certifies-presidential-election-results/",
)
BASE_2024 = "https://elections.maryland.gov/elections/archive/2024/election_data"


def build(files: dict[str, bytes], release=CERTIFIED_2024) -> pl.DataFrame:
    held = date.fromisoformat(release.election_date)
    return certified_2026.build(MD, files, release, MD.resources(held))


@pytest.fixture(scope="module")
def table():
    return build(md_files(FIXTURE_2024))


def statewide(table, office, mode="TOTAL"):
    return dict(
        table.filter((pl.col("office") == office) & (pl.col("mode") == mode)).group_by("candidate").agg(pl.col("candidatevotes").sum()).iter_rows()
    )


def test_file_names_and_urls():
    assert election_code(date(2024, 11, 5)) == "PG24"
    assert election_code(date(2022, 11, 8)) == "GG22"
    assert election_code(date(2026, 11, 3)) == "GG26"
    urls = MD.resources(date(2024, 11, 5))
    assert len(urls) == 24 and urls["03"] == f"{BASE_2024}/PG24_03CountyResults.csv"
    assert MD.resources(date(2026, 11, 3))["24"] == "https://elections.maryland.gov/elections/2026/election_data/GG26_24CountyResults.csv"


def test_output_matches_the_generated_contract(table):
    contract = parse_contract("electiondata", certified_2026.contract("MD"), "generated")
    assert table.columns == contract.columns
    assert validate_table(contract, table, None).status == "pass"


def test_statewide_senate_totals_match_the_certified_results(table):
    senate = statewide(table, "US SENATE")
    # Maryland's certified 2024 result; MEDSL's Senate file has the same figures for the three candidates.
    assert senate["ANGELA ALSOBROOKS"] == 1650912
    assert senate["LARRY HOGAN"] == 1294344
    assert senate["MIKE SCOTT"] == 69396
    assert senate["OTHER WRITE-INS"] == 5755
    assert senate["PATRICK J. BURKE"] == 879


def test_baltimore_city_is_its_own_county_equivalent(table):
    city = table.filter(pl.col("county_name") == "BALTIMORE CITY")
    county = table.filter(pl.col("county_name") == "BALTIMORE")
    assert set(city["county_id"]) == {"us-county-24510"}
    assert set(county["county_id"]) == {"us-county-24005"}
    assert set(city["source_url"]) == {f"{BASE_2024}/PG24_03CountyResults.csv"}
    assert set(county["source_url"]) == {f"{BASE_2024}/PG24_04CountyResults.csv"}
    assert sorted(table["county_name"].unique().to_list())[:3] == ["ALLEGANY", "ANNE ARUNDEL", "BALTIMORE"]
    assert "ST. MARY'S" in table["county_name"].to_list() and "PRINCE GEORGE'S" in table["county_name"].to_list()


def test_vote_modes(table):
    rows = table.filter(
        (pl.col("county_name") == "BALTIMORE CITY") & (pl.col("office") == "US SENATE") & (pl.col("candidate") == "ANGELA ALSOBROOKS")
    )
    modes = dict(rows.select("mode", "candidatevotes").iter_rows())
    # Early 44286, election night 71650, mail-in 29567 + 19811 (two canvasses), provisional 18615, total 183929.
    assert modes == {"EARLY VOTING": 44286, "ELECTION DAY": 71650, "MAIL-IN": 49378, "PROVISIONAL": 18615, "TOTAL": 183929}
    total = rows.filter(pl.col("mode") == "TOTAL").row(0, named=True)
    assert total["totalvotes"] == table.filter(
        (pl.col("county_name") == "BALTIMORE CITY") & (pl.col("office") == "US SENATE") & (pl.col("mode") == "TOTAL")
    )["candidatevotes"].sum()
    line = ["office", "district", "county_name", "candidate", "party_detailed"]
    by_mode = table.filter(pl.col("mode") != "TOTAL").group_by(line).agg(pl.col("candidatevotes").sum())
    totals = table.filter(pl.col("mode") == "TOTAL").select(*line, pl.col("candidatevotes").alias("total"))
    joined = by_mode.join(totals, on=line)
    assert len(joined) == len(totals) and (joined["candidatevotes"] == joined["total"]).all()


def test_contests_kept_and_skipped(table):
    offices = set(table.select("office", "district").unique().iter_rows())
    assert offices == {("US SENATE", "STATEWIDE"), *(("US HOUSE", f"{n:03d}") for n in range(1, 9))}
    # Baltimore City's own Comptroller and City Council President, the judicial retention vote and the school board are local.
    assert table.filter(pl.col("candidate").is_in(["BILL HENRY", "ZEKE COHEN", "ANNE K. ALBRIGHT", "BRENDA M. DIAZ"])).is_empty()
    house_6 = table.filter((pl.col("office") == "US HOUSE") & (pl.col("district") == "006"))
    assert sorted(house_6["county_name"].unique().to_list()) == ["ALLEGANY", "FREDERICK", "GARRETT", "MONTGOMERY", "WASHINGTON"]


def test_write_ins_and_parties(table):
    senate = table.filter((pl.col("office") == "US SENATE") & (pl.col("mode") == "TOTAL"))
    lines = dict(((c, p), (w, s)) for c, p, w, s in senate.select("candidate", "party_detailed", "writein", "party").unique().iter_rows())
    assert lines[("OTHER WRITE-INS", "")] == (True, "OTHER")
    assert lines[("PATRICK J. BURKE", "OTC")] == (True, "OTHER")
    assert lines[("ANGELA ALSOBROOKS", "DEM")] == (False, "DEMOCRAT")
    assert lines[("LARRY HOGAN", "REP")] == (False, "REPUBLICAN")
    assert lines[("MIKE SCOTT", "LIB")] == (False, "LIBERTARIAN")


def test_live_files_with_crlf_and_trailing_spaces(table):
    live = md_files(FIXTURE_2024, line_endings="crlf")
    assert live["01"].split(b"\r\n")[1].endswith(b'"" ')  # the space the Board's files end each line with
    assert build(live).equals(table)
    assert trim_line_ends(b'"a","" \r\n"b","" ') == b'"a",""\r\n"b",""'


def test_modes_that_do_not_add_up_are_rejected():
    files = md_files(FIXTURE_2024)
    line = next(l for l in files["05"].split(b"\n") if l.startswith(b'"U.S. Senator","","Angela Alsobrooks"'))
    cells = line.split(b",")
    cells[16] = b'"' + str(int(cells[16].strip(b'"')) + 1).encode() + b'"'
    files["05"] = files["05"].replace(line, b",".join(cells))
    with pytest.raises(ValueError, match="don't add up to the TOTAL"):
        build(files)


def test_missing_county_file_is_rejected():
    files = md_files(FIXTURE_2024)
    del files["20"]
    with pytest.raises(ValueError, match=r"missing for county codes \['20'\]"):
        MD.parse(files)
    with pytest.raises(ValueError, match="don't match resources"):
        build(files)


def test_2022_gubernatorial_offices():
    table = build(md_files(FIXTURE_2022), certified_2026.Release("MD", election_date="2022-11-08"))
    governor = statewide(table, "GOVERNOR")
    assert governor["WES MOORE AND ARUNA MILLER"] == 1293944
    assert governor["DAN COX AND GORDANA SCHIFANELLI"] == 644000
    assert statewide(table, "COMPTROLLER")["BROOKE ELIZABETH LIERMAN"] == 1223044
    assert table["county_name"].n_unique() == len(COUNTIES) == 24
    assert set(table["status"]) == {"unofficial"}


def test_statewide_office_in_only_some_counties_is_rejected():
    files = md_files(FIXTURE_2022)
    files["05"] = b"\n".join(l for l in files["05"].split(b"\n") if not l.startswith(b'"Comptroller"'))
    with pytest.raises(ValueError, match="'Comptroller' appears in only 23 county files"):
        build(files, certified_2026.Release("MD", election_date="2022-11-08"))
