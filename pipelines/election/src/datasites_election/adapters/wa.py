"""Washington: the Secretary of State's statewide results, by county.

From 2026, results are on results.votewa.gov, a site Enhanced Voting runs for the Secretary of State. Its
API (https://results.votewa.gov/results/public/api/elections/washington/<YYYYMMDD>) lists the election's
reports; "All Results Excel" is an XLSX whose "Precinct Results" sheet has one row per county (the
"Precinct" column holds "Adams County"), contest and ballot line:
    Precinct, Office Name, Contest ID, Ballot Name, Choice ID, Party, Total
Each contest also has "Ballots Cast", "Over Votes" and "Under Votes" lines, which are not candidates and
are skipped. The report's file name carries a new id each time the state republishes, so locate() reads
it from the API; the file is at https://results.votewa.gov/cdn/results/<jurisdiction id>/<file name>.
There is no jurisdiction column, so contests are told apart by name (federal, statewide executive and
Supreme Court); a name that looks federal or statewide and isn't recognized stops the run.

Up to 2024 (the old site still serves these):
https://results.vote.wa.gov/results/<YYYYMMDD>/export/<YYYYMMDD>_AllCounties.csv has one row per county,
contest and candidate for every contest on the ballot, with these columns:
    "County","Race","Candidate","Party","Votes","PercentageOfTotalVotes","JurisdictionName"
The layout has been the same since at least 2018. The same URL serves the unofficial count from
election night, and the certified count once the Secretary of State certifies (2024: December 4).

What the adapter takes: JurisdictionName "Federal" (President, US Senate), "Congressional" (US House),
"State Executive" offices (not the statewide measures) and the statewide Supreme Court positions under
"Judicial". Everything else (legislative, county, city and district contests) is local and skipped.
Contest names are matched exactly or by pattern; a federal or statewide-executive contest the adapter
doesn't recognize stops the run, so nothing is silently dropped.

Washington reports no vote-mode breakdown, so every row is mode TOTAL. Party is the candidate's stated
preference ("(Prefers Democratic Party)") in a top-two system, or the nominating party for President.
Each contest has one WRITE-IN line, the county's total of all write-in votes.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from datetime import date
from urllib.parse import quote

import fastexcel
import polars as pl

from .. import county_schema
from .base import SourceInfo, StateAdapter, decode, office_number, whole_votes

COLUMNS = ["County", "Race", "Candidate", "Party", "Votes", "PercentageOfTotalVotes", "JurisdictionName"]
FEDERAL = {
    "United States President/Vice President": "US PRESIDENT",
    "United States U.S. Senator": "US SENATE",
}
HOUSE = re.compile(r"(?i)^congressional district (\d+) u\.s\. representative$")
SUPREME_COURT = re.compile(r"(?i)^supreme court justice position (#?\d+)$")
STATE_EXECUTIVE = {
    "Governor": "GOVERNOR",
    "Lt. Governor": "LIEUTENANT GOVERNOR",
    "Secretary of State": "SECRETARY OF STATE",
    "State Treasurer": "STATE TREASURER",
    "State Auditor": "STATE AUDITOR",
    "Attorney General": "ATTORNEY GENERAL",
    "Commissioner of Public Lands": "COMMISSIONER OF PUBLIC LANDS",
    "Superintendent of Public Instruction": "SUPERINTENDENT OF PUBLIC INSTRUCTION",
    "Insurance Commissioner": "INSURANCE COMMISSIONER",
}
MEASURE = re.compile(r"(?i)\b(initiative|referendum|advisory vote|joint resolution|measure|amendment)\b")
WRITE_IN = "WRITE-IN"

# The results.votewa.gov export (2026 on).
NEW_SITE = "https://results.votewa.gov"
FIRST_NEW_SITE_YEAR = 2026
REPORT = "All Results Excel"
SHEET = "Precinct Results"
XLSX_COLUMNS = ["Precinct", "Office Name", "Contest ID", "Ballot Name", "Choice ID", "Party", "Total"]
NOT_CANDIDATES = {"BALLOTS CAST", "OVER VOTES", "UNDER VOTES"}
XLSX_HOUSE = re.compile(r"(?i)^congressional district (\d+) - u\.s\. representative$")
XLSX_SUPREME_COURT = re.compile(r"(?i)^supreme court - justice position (#?\d+)$")
XLSX_FEDERAL = {
    re.compile(r"(?i)^(united states )?president.*vice president$"): "US PRESIDENT",
    re.compile(r"(?i)^(united states )?u\.s\. senator( - .*)?$"): "US SENATE",
}
# Names that must be classified if they appear: anything that looks federal or statewide.
LOOKS_FEDERAL_OR_STATEWIDE = re.compile(r"(?i)(\bu\.s\.|\bunited states\b|\bcongressional\b|\bpresident\b|\bsenator\b)")


class Washington(StateAdapter):
    state_po = "WA"
    source = SourceInfo(
        id="wa-sos-results",
        name="Washington Secretary of State, official election results by county",
        publisher="Washington Office of the Secretary of State, Elections Division",
        url="https://results.votewa.gov/results/public/washington",
        # The Secretary of State's "Public Domain & Copyright Information" (re-read 2026-11-26): "This is a public
        # domain web site." results.votewa.gov is the Secretary of State's results site (run by Enhanced Voting).
        terms_url="https://www.sos.wa.gov/web-sites-privacy-policy",
        license="LicenseRef-Public-Domain",
        commercial_ok=True,
        attribution="Office of the Secretary of State, State of Washington (results.votewa.gov).",
    )

    def resources(self, election_date: date) -> dict[str, str]:
        if election_date.year >= FIRST_NEW_SITE_YEAR:
            raise ValueError(f"WA {election_date}: results.votewa.gov file names must be looked up; use locate()")
        stamp = election_date.strftime("%Y%m%d")
        return {"AllCounties": f"https://results.vote.wa.gov/results/{stamp}/export/{stamp}_AllCounties.csv"}

    def locate(self, election_date: date, get: Callable[[str], bytes]) -> dict[str, str]:
        if election_date.year < FIRST_NEW_SITE_YEAR:
            return self.resources(election_date)
        stamp = election_date.strftime("%Y%m%d")
        return {"AllResults": report_url(json.loads(get(f"{NEW_SITE}/results/public/api/elections/washington/{stamp}")), stamp)}

    def parse(self, raw: dict[str, bytes]) -> pl.DataFrame:
        if set(raw) == {"AllResults"}:
            return self._parse_xlsx(raw["AllResults"])
        what = "WA AllCounties"
        df = pl.read_csv(io.StringIO(decode(raw["AllCounties"])), infer_schema_length=0)
        if df.columns != COLUMNS:
            raise ValueError(f"{what}: columns changed: {df.columns}")
        df = df.with_columns(pl.col(c).fill_null("").str.strip_chars() for c in COLUMNS)

        offices: dict[tuple[str, str], tuple[str, str] | None] = {}
        for jurisdiction, race in df.select("JurisdictionName", "Race").unique().iter_rows():
            offices[(jurisdiction, race)] = classify(jurisdiction, race)
        mapping = pl.DataFrame(
            [(j, r, o[0], o[1]) for (j, r), o in offices.items() if o is not None],
            schema={"JurisdictionName": pl.Utf8, "Race": pl.Utf8, "office": pl.Utf8, "district": pl.Utf8},
            orient="row",
        )
        df = df.join(mapping, on=["JurisdictionName", "Race"], how="inner")

        registry = {loc["name"]: fips for fips, loc in county_schema.counties(self.state_po).items()}
        names = sorted(set(df["County"].to_list()))
        unknown = [n for n in names if f"{n} County" not in registry]
        if unknown:
            raise ValueError(f"{what}: counties not in the registry: {unknown}")
        fips = {n: registry[f"{n} County"] for n in names}

        return df.select(
            pl.lit("AllCounties").alias("resource"),
            pl.col("County").replace_strict(fips).alias("county_fips"),
            pl.col("County").map_elements(county_schema.county_label, return_dtype=pl.Utf8).alias("county_name"),
            "office",
            "district",
            pl.col("Candidate").str.to_uppercase().alias("candidate"),
            pl.col("Party").str.to_uppercase().alias("party_detailed"),
            (pl.col("Candidate").str.to_uppercase() == WRITE_IN).alias("writein"),
            pl.lit("TOTAL").alias("mode"),
            whole_votes(df["Votes"], what).alias("candidatevotes"),
        )


    def _parse_xlsx(self, body: bytes) -> pl.DataFrame:
        what = "WA All Results"
        reader = fastexcel.read_excel(body)
        if SHEET not in reader.sheet_names:
            raise ValueError(f"{what}: no {SHEET!r} sheet (have {reader.sheet_names})")
        df = reader.load_sheet(SHEET, dtypes="string").to_polars()
        if df.columns != XLSX_COLUMNS:
            raise ValueError(f"{what}: columns changed: {df.columns}")
        df = df.with_columns(pl.col(c).fill_null("").str.strip_chars() for c in XLSX_COLUMNS)
        df = df.filter(~pl.col("Ballot Name").str.to_uppercase().is_in(NOT_CANDIDATES))

        offices = {name: classify_office(name) for name in df["Office Name"].unique().to_list()}
        mapping = pl.DataFrame(
            [(name, o[0], o[1]) for name, o in offices.items() if o is not None],
            schema={"Office Name": pl.Utf8, "office": pl.Utf8, "district": pl.Utf8},
            orient="row",
        )
        df = df.join(mapping, on="Office Name", how="inner")

        registry = {loc["name"]: fips for fips, loc in county_schema.counties(self.state_po).items()}
        names = sorted(set(df["Precinct"].to_list()))
        unknown = [n for n in names if n not in registry]
        if unknown:
            raise ValueError(f"{what}: counties not in the registry: {unknown}")
        # Totals are whole numbers stored as numbers ("4420" or "4420.0" once read as text).
        votes = df["Total"].str.replace(r"\.0+$", "")
        return df.select(
            pl.lit("AllResults").alias("resource"),
            pl.col("Precinct").replace_strict(registry).alias("county_fips"),
            pl.col("Precinct").str.strip_suffix(" County").map_elements(county_schema.county_label, return_dtype=pl.Utf8).alias("county_name"),
            "office",
            "district",
            pl.col("Ballot Name").str.to_uppercase().alias("candidate"),
            pl.col("Party").str.to_uppercase().alias("party_detailed"),
            (pl.col("Ballot Name").str.to_uppercase() == WRITE_IN).alias("writein"),
            pl.lit("TOTAL").alias("mode"),
            whole_votes(votes, what).alias("candidatevotes"),
        )


def report_url(election: dict, stamp: str) -> str:
    """The All Results report's URL, from the results.votewa.gov API's election record."""
    if election.get("electionDate") != f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}":
        raise ValueError(f"WA: the API answered for election {election.get('electionDate')!r}, not {stamp}")
    reports = [r for c in election.get("publicReportCategories") or [] for r in c.get("reports") or []]
    matches = [r["blobName"] for r in reports if r.get("reportName") == REPORT]
    if len(matches) != 1:
        raise ValueError(f"WA {stamp}: expected one {REPORT!r} report, found {[r.get('reportName') for r in reports]}")
    return f"{NEW_SITE}/cdn/results/{election['jurisdictionId']}/{quote(matches[0])}"


def classify_office(name: str) -> tuple[str, str] | None:
    """(office, district) for a federal or statewide contest in the results.votewa.gov export; None otherwise."""
    if m := XLSX_HOUSE.match(name):
        return "US HOUSE", county_schema.district(m.group(1))
    for pattern, office in XLSX_FEDERAL.items():
        if pattern.match(name):
            return office, "STATEWIDE"
    if m := XLSX_SUPREME_COURT.match(name):
        return f"STATE SUPREME COURT JUSTICE POSITION {office_number(m.group(1))}", "STATEWIDE"
    office = name.removeprefix("Washington State ").strip()
    if office in STATE_EXECUTIVE:
        return STATE_EXECUTIVE[office], "STATEWIDE"
    if MEASURE.search(name):
        return None
    if LOOKS_FEDERAL_OR_STATEWIDE.search(name) and not name.lower().startswith("legislative district"):
        raise ValueError(f"WA: unrecognized federal or statewide contest {name!r}; add it to adapters/wa.py")
    return None


def classify(jurisdiction: str, race: str) -> tuple[str, str] | None:
    """(office, district) for a federal or statewide contest; None for local ones and measures."""
    if jurisdiction == "Federal":
        if race not in FEDERAL:
            raise ValueError(f"WA: unrecognized federal contest {race!r}; add it to adapters/wa.py")
        return FEDERAL[race], "STATEWIDE"
    if jurisdiction == "Congressional":
        m = HOUSE.match(race)
        if not m:
            raise ValueError(f"WA: unrecognized congressional contest {race!r}; add it to adapters/wa.py")
        return "US HOUSE", county_schema.district(m.group(1))
    if jurisdiction == "State Executive":
        if MEASURE.search(race):
            return None
        office = race.removeprefix("Washington State ").strip()
        if office not in STATE_EXECUTIVE:
            raise ValueError(f"WA: unrecognized statewide contest {race!r}; add it to adapters/wa.py")
        return STATE_EXECUTIVE[office], "STATEWIDE"
    if jurisdiction == "Judicial":
        m = SUPREME_COURT.match(race)
        return (f"STATE SUPREME COURT JUSTICE POSITION {office_number(m.group(1))}", "STATEWIDE") if m else None
    return None
