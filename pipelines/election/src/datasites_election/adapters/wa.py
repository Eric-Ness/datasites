"""Washington: the Secretary of State's statewide "All Counties" results export.

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
import re
from datetime import date

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


class Washington(StateAdapter):
    state_po = "WA"
    source = SourceInfo(
        id="wa-sos-results",
        name="Washington Secretary of State, election results export (All Counties)",
        publisher="Washington Office of the Secretary of State, Elections Division",
        url="https://results.vote.wa.gov/",
        terms_url="https://www.sos.wa.gov/node/133",
        license="LicenseRef-Public-Domain",
        commercial_ok=True,
        attribution="Office of the Secretary of State, State of Washington (results.vote.wa.gov).",
    )

    def resources(self, election_date: date) -> dict[str, str]:
        stamp = election_date.strftime("%Y%m%d")
        return {"AllCounties": f"https://results.vote.wa.gov/results/{stamp}/export/{stamp}_AllCounties.csv"}

    def parse(self, raw: dict[str, bytes]) -> pl.DataFrame:
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
