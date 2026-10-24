"""The county-level results schema shared by electiondata's county datasets.

It follows MEDSL's county presidential file (year, state, state_po, county_name, county_fips, office,
candidate, party, candidatevotes, totalvotes, mode) with the same meanings and spelling conventions:
upper-case names, office names such as US PRESIDENT, US SENATE and US HOUSE, and party simplified to
DEMOCRAT, REPUBLICAN, LIBERTARIAN, GREEN or OTHER. It adds what a multi-office file needs:
- county_id, the registry id (us-county-<fips>);
- district: STATEWIDE, or a zero-padded number such as 007;
- stage and special;
- party_detailed, the party line exactly as the source prints it;
- writein.

The certified-results importer adds status, source_url and certified_at (see certified_2026.py).

mode: every county has a TOTAL row for each ballot line. Where a state also reports how the votes were
cast, there are extra rows (EARLY VOTING, ELECTION DAY, MAIL-IN, PROVISIONAL) that add up to that
TOTAL. Filter on mode = TOTAL to count votes; never sum across modes.

totalvotes: all votes for every ballot line in the contest, in that county and mode, including
write-ins, so candidatevotes / totalvotes is the vote share. Blank and over-votes are not included.

Fusion voting (New York, Connecticut): a candidate nominated by several parties has one row per party
line, told apart by party_detailed, and the minor lines' party is OTHER. Sum candidatevotes by candidate
for the candidate's total; summing by party credits those votes to OTHER.

Units that aren't counties: Alaska reports by state house district and New England states by town. An
adapter rolls towns up to the registry's county equivalents (Connecticut's are the nine planning
regions, 09110-09190, which replaced its counties in Census geography in 2022). Where that isn't
possible (Alaska), county_fips and county_id are null and county_name is the source's unit.
"""

from __future__ import annotations

import re

import polars as pl

from datasites_core import geo

# (name, contract type, nullable, description)
COLUMNS: list[tuple[str, str, bool, str]] = [
    ("year", "year", False, "Year of the election"),
    ("state", "str", False, "State name in upper case"),
    ("state_po", "str", False, "USPS state code"),
    ("county_name", "str", False, "County or county-equivalent name in upper case, without the word COUNTY"),
    ("county_fips", "str", True, "Five-digit county FIPS code; null where the source's unit is not a county"),
    ("county_id", "str", True, "Location id in the shared registry (us-county-<fips>)"),
    ("office", "str", False, "Office, e.g. US PRESIDENT, US SENATE, US HOUSE, GOVERNOR"),
    ("district", "str", False, "STATEWIDE, or the district number zero-padded to three digits"),
    ("stage", "str", False, "GEN for a general election, RUNOFF for a runoff"),
    ("special", "bool", False, "True for a special election to fill a vacancy"),
    ("candidate", "str", False, "Candidate or ticket as the source prints it, upper case; write-in totals use the source's own label"),
    ("party_detailed", "str", False, "Party line exactly as the source prints it, upper case; empty where the source prints none"),
    ("party", "str", False, "DEMOCRAT, REPUBLICAN, LIBERTARIAN, GREEN, OTHER or NONPARTISAN"),
    ("writein", "bool", False, "True for write-in votes"),
    ("mode", "str", False, "TOTAL, or a vote mode that is part of the TOTAL (EARLY VOTING, ELECTION DAY, MAIL-IN, PROVISIONAL)"),
    ("candidatevotes", "int", True, "Votes for this ballot line"),
    ("totalvotes", "int", True, "Votes for all ballot lines in the contest, in this county and mode"),
]

KEY = ["year", "state_po", "stage", "office", "district", "special", "county_name", "candidate", "party_detailed", "mode"]
MODES = ("TOTAL", "EARLY VOTING", "ELECTION DAY", "MAIL-IN", "PROVISIONAL")
PARTIES = ("DEMOCRAT", "REPUBLICAN", "LIBERTARIAN", "GREEN", "OTHER", "NONPARTISAN")


def names() -> list[str]:
    return [name for name, *_ in COLUMNS]


def district(number: int | str | None) -> str:
    """None -> STATEWIDE; 7 or '07' -> '007'."""
    if number is None or str(number).strip() == "":
        return "STATEWIDE"
    text = str(number).strip()
    if not text.isdigit():
        raise ValueError(f"district {number!r} is not a number")
    return text.zfill(3)


def counties(state_po: str) -> dict[str, dict]:
    """FIPS -> registry entry for every county or county-equivalent of a state."""
    parent = f"us-{state_po.lower()}"
    return {loc["fips"]: loc for loc in geo.load().values() if loc["level"] == "us-county" and loc.get("parent") == parent}


def state_name(state_po: str) -> str:
    loc = geo.location(f"us-{state_po.lower()}")
    if loc is None:
        raise ValueError(f"{state_po} is not a state in the registry")
    return loc["name"].upper()


def county_label(name: str) -> str:
    """'Baltimore County' -> 'BALTIMORE'; 'Baltimore City' -> 'BALTIMORE CITY'; 'King' -> 'KING'."""
    return re.sub(r"\s+COUNTY$", "", " ".join(name.split()).upper())


def simplify_party(label: str) -> str:
    """A state's party label -> MEDSL's simplified party. Unrecognized parties are OTHER."""
    text = " ".join(label.upper().replace("(", " ").replace(")", " ").split())
    text = re.sub(r"^PREFERS\s+|\s+PARTY(\s+NOMINEES?)?$|\s+NOMINEES?$", "", text)
    if text in ("DEMOCRAT", "DEMOCRATIC", "DEM"):
        return "DEMOCRAT"
    if text in ("REPUBLICAN", "GOP", "REPUBLICAN-GOP", "REP"):
        return "REPUBLICAN"
    if text in ("LIBERTARIAN", "LIB"):
        return "LIBERTARIAN"
    if text in ("GREEN", "GRN"):
        return "GREEN"
    return "OTHER"


def finish(frame: pl.DataFrame) -> pl.DataFrame:
    """Columns in schema order, with types the contract expects."""
    types = {"year": pl.Utf8, "str": pl.Utf8, "bool": pl.Boolean, "int": pl.Int64}
    return frame.select(pl.col(name).cast(types[kind]) for name, kind, *_ in COLUMNS)
