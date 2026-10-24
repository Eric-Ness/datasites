"""Maryland: the State Board of Elections' county results files, one per county.

Election Law 11-601 requires the State Board to publish each election's results for each county as a
whole. For a general election the files are <code>_<NN>CountyResults.csv, where <code> is PG<yy> in
presidential years and GG<yy> in gubernatorial years (GG22, PG24, GG26) and NN is the county code
from the Board's "Using Election Data" Appendix A (01 Allegany ... 24 Worcester; 03 is Baltimore City,
an independent city with its own FIPS code, 24510). The 2022 and 2024 files share this header:
    "Office Name","Office District","Candidate Name","Party","Winner","Write-In?","Early Votes",
    "Early Votes Against","Election Night Votes","Election Night Votes Against","Mail-In Ballot 1 Votes",
    "Mail-In Ballot 1 Votes Against","Provisional Votes","Provisional Votes Against",
    "Mail-In Ballot 2 Votes","Mail-In Ballot 2 Votes Against","Total Votes","Total Votes Against"
Every line ends with a space after its last quoted field, which strict CSV readers reject, so that is
trimmed before parsing.

Vote modes: Early Votes -> EARLY VOTING, Election Night Votes -> ELECTION DAY, the two mail-in canvasses
(Mail-In Ballot 1 and 2) -> MAIL-IN, Provisional Votes -> PROVISIONAL. They add up to Total Votes,
which is checked. The "Against" columns are only used for judicial retention and ballot questions,
which the adapter skips.

What the adapter takes: President, US Senate, US House (by district), Governor, Comptroller and
Attorney General. Baltimore City elects a city Comptroller in presidential years under the same office
name, so a statewide office name counts only when it appears in all 24 county files. Declared
write-in candidates have their own rows (Write-In? = Y) next to the county's "Other Write-Ins" line.

During the canvass the files are under /elections/<year>/election_data/. After the cycle the Board
moves them to /elections/archive/<year>/election_data/ (done for 2022 and 2024); add a year to
ARCHIVED when that happens.
"""

from __future__ import annotations

import io
import re
from datetime import date

import polars as pl

from .. import county_schema
from .base import SourceInfo, StateAdapter, decode, office_number, whole_votes

SITE = "https://elections.maryland.gov"
ARCHIVED = {2022, 2024}
# Appendix A of https://elections.maryland.gov/elections/using_election_data_instructions.html, with FIPS codes.
COUNTIES = {
    "01": ("Allegany County", "24001"),
    "02": ("Anne Arundel County", "24003"),
    "03": ("Baltimore City", "24510"),
    "04": ("Baltimore County", "24005"),
    "05": ("Calvert County", "24009"),
    "06": ("Caroline County", "24011"),
    "07": ("Carroll County", "24013"),
    "08": ("Cecil County", "24015"),
    "09": ("Charles County", "24017"),
    "10": ("Dorchester County", "24019"),
    "11": ("Frederick County", "24021"),
    "12": ("Garrett County", "24023"),
    "13": ("Harford County", "24025"),
    "14": ("Howard County", "24027"),
    "15": ("Kent County", "24029"),
    "16": ("Montgomery County", "24031"),
    "17": ("Prince George's County", "24033"),
    "18": ("Queen Anne's County", "24035"),
    "19": ("St. Mary's County", "24037"),
    "20": ("Somerset County", "24039"),
    "21": ("Talbot County", "24041"),
    "22": ("Washington County", "24043"),
    "23": ("Wicomico County", "24045"),
    "24": ("Worcester County", "24047"),
}
BALTIMORE_CITY = "03"
COLUMNS = [
    "Office Name", "Office District", "Candidate Name", "Party", "Winner", "Write-In?", "Early Votes",
    "Early Votes Against", "Election Night Votes", "Election Night Votes Against", "Mail-In Ballot 1 Votes",
    "Mail-In Ballot 1 Votes Against", "Provisional Votes", "Provisional Votes Against", "Mail-In Ballot 2 Votes",
    "Mail-In Ballot 2 Votes Against", "Total Votes", "Total Votes Against",
]  # fmt: skip
MODES = {
    "EARLY VOTING": ["Early Votes"],
    "ELECTION DAY": ["Election Night Votes"],
    "MAIL-IN": ["Mail-In Ballot 1 Votes", "Mail-In Ballot 2 Votes"],
    "PROVISIONAL": ["Provisional Votes"],
    "TOTAL": ["Total Votes"],
}
STATEWIDE = {
    "President - Vice Pres": "US PRESIDENT",
    "U.S. Senator": "US SENATE",
    "Governor / Lt. Governor": "GOVERNOR",
    "Comptroller": "COMPTROLLER",
    "Attorney General": "ATTORNEY GENERAL",
}
HOUSE = "U.S. Congress"
# Not plain "President" or "Senator": Baltimore City has a "President City Council" and the state a "State Senator".
FEDERAL_LOOKING = re.compile(r"(?i)\bu\.\s?s\.|united states|congress|vice pres")


def election_code(election_date: date) -> str:
    return ("PG" if election_date.year % 4 == 0 else "GG") + election_date.strftime("%y")


def trim_line_ends(body: bytes) -> bytes:
    """The Board's files end every line with a space after the closing quote ('..."" \\r\\n')."""
    return re.sub(rb"[ \t]+(\r?\n|$)", rb"\1", body)


class Maryland(StateAdapter):
    state_po = "MD"
    source = SourceInfo(
        id="md-sbe-results",
        name="Maryland State Board of Elections, general election county results files",
        publisher="Maryland State Board of Elections",
        url=f"{SITE}/elections/using_election_data_instructions.html",
        terms_url=f"{SITE}/privacy.html",
        license="LicenseRef-Public-Record",
        commercial_ok=None,
        attribution="Maryland State Board of Elections, official general election results by county (elections.maryland.gov).",
    )

    def _base(self, election_date: date) -> str:
        archive = "archive/" if election_date.year in ARCHIVED else ""
        return f"{SITE}/elections/{archive}{election_date.year}/election_data"

    def resources(self, election_date: date) -> dict[str, str]:
        code, base = election_code(election_date), self._base(election_date)
        return {nn: f"{base}/{code}_{nn}CountyResults.csv" for nn in COUNTIES}

    def parse(self, raw: dict[str, bytes]) -> pl.DataFrame:
        missing = sorted(set(COUNTIES) - set(raw))
        if missing:
            raise ValueError(f"MD: county results files missing for county codes {missing}")
        frames = []
        for nn, body in sorted(raw.items()):
            if nn not in COUNTIES:
                raise ValueError(f"MD: unexpected file {nn!r}")
            what = f"MD county file {nn}"
            df = pl.read_csv(io.StringIO(decode(trim_line_ends(body))), infer_schema_length=0)
            if [c.strip() for c in df.columns] != COLUMNS:
                raise ValueError(f"{what}: columns changed: {df.columns}")
            df.columns = COLUMNS
            frames.append(df.with_columns(pl.col(c).fill_null("").str.strip_chars() for c in COLUMNS).with_columns(pl.lit(nn).alias("nn")))
        df = pl.concat(frames)

        offices = df.group_by("Office Name").agg(pl.col("nn").unique().alias("counties"))
        keep = {HOUSE}
        for name, present in offices.iter_rows():
            if name in STATEWIDE:
                if len(present) == len(COUNTIES):
                    keep.add(name)
                elif set(present) != {BALTIMORE_CITY}:
                    raise ValueError(f"MD: statewide office {name!r} appears in only {len(present)} county files: {sorted(present)}")
            elif name != HOUSE and FEDERAL_LOOKING.search(name):
                raise ValueError(f"MD: unrecognized federal contest {name!r}; add it to adapters/md.py")
        df = df.filter(pl.col("Office Name").is_in(sorted(keep)))

        against = [c for c in COLUMNS if c.endswith("Against")]
        voted_against = df.filter(pl.any_horizontal(pl.col(c) != "" for c in against))
        if len(voted_against):
            raise ValueError(f"MD: 'Against' votes in a candidate contest: {voted_against.row(0, named=True)}")
        bad_flags = sorted(set(df["Write-In?"].unique().to_list()) - {"", "Y"})
        if bad_flags:
            raise ValueError(f"MD: unexpected Write-In? values {bad_flags}")
        votes = {c: whole_votes(df[c].replace("", "0") if c != "Total Votes" else df[c], "MD") for c in sum(MODES.values(), [])}
        df = df.with_columns(pl.Series(c, v) for c, v in votes.items())

        houses = df.filter(pl.col("Office Name") == HOUSE)["Office District"].unique().to_list()
        districts = {d: county_schema.district(office_number(d)) for d in houses}
        lines = df.select(
            pl.col("nn").alias("resource"),
            pl.col("nn").replace_strict({nn: fips for nn, (_, fips) in COUNTIES.items()}).alias("county_fips"),
            pl.col("nn").replace_strict({nn: county_schema.county_label(name) for nn, (name, _) in COUNTIES.items()}).alias("county_name"),
            pl.when(pl.col("Office Name") == HOUSE).then(pl.lit("US HOUSE")).otherwise(pl.col("Office Name").replace(STATEWIDE)).alias("office"),
            pl.when(pl.col("Office Name") == HOUSE)
            .then(pl.col("Office District").replace(districts))
            .otherwise(pl.lit("STATEWIDE"))
            .alias("district"),
            pl.col("Candidate Name").str.to_uppercase().alias("candidate"),
            pl.col("Party").str.to_uppercase().alias("party_detailed"),
            (pl.col("Write-In?") == "Y").alias("writein"),
            *[pl.sum_horizontal(columns).alias(mode) for mode, columns in MODES.items()],
        )
        return lines.unpivot(index=lines.columns[:8], on=list(MODES), variable_name="mode", value_name="candidatevotes").select(
            "resource", "county_fips", "county_name", "office", "district", "candidate", "party_detailed", "writein", "mode",
            pl.col("candidatevotes").cast(pl.Int64),
        )  # fmt: skip
