"""Yearly building permits for every county from the Census Bureau's Building Permits Survey (1990-).

One file per year, County/co{YYYY}a.txt: two header lines, then comma-separated rows of
  survey date, state FIPS, county FIPS, region, division, county name,
  then buildings, units and value for 1-unit, 2-unit, 3-4 unit and 5+ unit buildings (estimates with
  imputation for permit offices that didn't report), then the same twelve figures as reported only.
The 1990s files pad every field with spaces and write the date as YY99 ("9099" for 1990); later ones write
the year. Only the estimates (the first twelve figures) are kept: they cover every permit office.

Rows that aren't a county are kept out of the county series but counted in the state and US totals:
- "Balance of State" rows (county code 000, 099 or 999), for permit offices not tied to one county;
- Puerto Rico and the Virgin Islands in 1999-2000, under old state codes 43 and 52, are left out entirely.
Former counties keep their codes (Connecticut's eight counties to 2022, Dade County, Alaska's old census
areas, Virginia's merged cities). In 2014 and 2015 five counties appear twice under two spellings with the
same figures; one row is kept. State and US rows are sums of the county files, not BPS's own state files.
From 2004 they equal the state files for every state and DC (scripts/verify/housing.mjs checks it; Alaska 2013
is 26 units short). Before 2004 the county files cover fewer permit offices than the state files: about 0.1%
fewer units nationally in most years, but 2.5-3% in 1991-1993, and far more in a few states.
"""

from __future__ import annotations

import csv
import io
import re

import polars as pl

from datasites_core import geo

BPS = "https://www2.census.gov/econ/bps/County"
FIRST_YEAR = 1990
SERIES = "permits"
FILE = re.compile(r"\bco(\d{4})a\.txt\b")
NAME = re.compile(r"^bps-(\d{4})$")
GROUPS = ["1", "2", "3_4", "5_plus"]
FIGURES = [f"{what}_{group}" for group in GROUPS for what in ("buildings", "units", "value")]
FIELDS = 30  # 6 identifiers, 12 estimates, 12 reported-only figures
LEFT_OUT_STATES = {"43", "52"}  # Puerto Rico and the Virgin Islands under pre-2001 codes
BALANCE = {"000", "099", "999"}


def resources(ctx) -> dict[str, str]:
    """Every annual county file from 1990 on, as listed in the directory (conditional GETs keep reruns cheap)."""
    listing = ctx.get(f"{BPS}/").decode("utf-8", "replace")
    years = sorted({int(y) for y in FILE.findall(listing) if int(y) >= FIRST_YEAR})
    if not years or years[0] != FIRST_YEAR:
        raise ValueError(f"BPS County directory: expected annual files from {FIRST_YEAR}, found {years[:3]}")
    missing = sorted(set(range(FIRST_YEAR, years[-1] + 1)) - set(years))
    if missing:
        raise ValueError(f"BPS County directory: annual files missing for {missing}")
    return {f"bps-{y}": f"{BPS}/co{y}a.txt" for y in years}


def parse_year(body: bytes, year: int) -> pl.DataFrame:
    rows = list(csv.reader(io.StringIO(body.decode("latin-1"))))
    header = [c.strip() for c in rows[1]] if len(rows) > 1 else []
    if header[:6] != ["Date", "State", "County", "Code", "Code", "Name"]:
        raise ValueError(f"BPS {year}: unexpected header {header[:6]}")
    records = []
    for row in rows[2:]:
        cells = [c.strip() for c in row]
        if not "".join(cells):
            continue
        if len(cells) != FIELDS:
            raise ValueError(f"BPS {year}: a row has {len(cells)} fields, expected {FIELDS}: {cells[:6]}")
        date, state, county, _, _, name = cells[:6]
        if date not in (str(year), f"{year % 100:02d}99"):
            raise ValueError(f"BPS {year}: survey date {date!r} in co{year}a.txt")
        if not (state.isdigit() and county.isdigit()):
            raise ValueError(f"BPS {year}: FIPS codes {state!r} {county!r} for {name!r}")
        figures = cells[6:18]
        if not all(f.isdigit() for f in figures):
            raise ValueError(f"BPS {year}: non-numeric figures for {name!r}: {figures}")
        records.append((state.zfill(2), county.zfill(3), name, *map(int, figures)))
    df = pl.DataFrame(records, schema=["state", "county", "name", *FIGURES], orient="row")
    if not len(df):
        raise ValueError(f"BPS {year}: no rows")
    # The same county under two spellings (2014-2015): identical figures collapse to one row; anything else stops.
    by_code = df.unique(subset=["state", "county", *FIGURES]).group_by("state", "county").len().filter(pl.col("len") > 1)
    if len(by_code):
        raise ValueError(f"BPS {year}: counties listed twice with different figures: {by_code.head(5).rows()}")
    return df.unique(subset=["state", "county"], keep="first", maintain_order=True).with_columns(pl.lit(str(year)).alias("date"))


def totals(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        (pl.col("units_1") + pl.col("units_2") + pl.col("units_3_4") + pl.col("units_5_plus")).alias("units"),
        (pl.col("buildings_1") + pl.col("buildings_2") + pl.col("buildings_3_4") + pl.col("buildings_5_plus")).alias("buildings"),
        (pl.col("value_1") + pl.col("value_2") + pl.col("value_3_4") + pl.col("value_5_plus")).alias("value"),
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name in sorted(raw):
        m = NAME.match(name)
        if m:
            frames.append(parse_year(raw[name], int(m.group(1))))
    if not frames:
        raise ValueError("BPS: no annual files")
    df = pl.concat(frames).filter(~pl.col("state").is_in(list(LEFT_OUT_STATES)))

    states = {loc["fips"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}
    unknown = sorted(set(df["state"].unique()) - set(states))
    if unknown:
        raise ValueError(f"BPS: state codes not in the registry: {unknown}")

    counties = df.filter(~pl.col("county").is_in(list(BALANCE))).with_columns(
        (pl.lit("us-county-") + pl.col("state") + pl.col("county")).alias("location")
    )
    by_state = df.group_by("state", "date").agg(pl.col(FIGURES).sum()).with_columns(
        pl.col("state").replace_strict(states).alias("location")
    )
    national = df.group_by("date").agg(pl.col(FIGURES).sum()).with_columns(pl.lit("usa").alias("location"))
    columns = ["location", "date", "units", "units_1", "units_2", "units_3_4", "units_5_plus", "buildings", "value"]
    out = pl.concat([totals(f).select(columns) for f in (counties, by_state, national)])
    return out.with_columns(pl.lit(SERIES).alias("series")).select("series", *columns)
