"""Daily AQI by US county: validated EPA AirData history (1980-) extended with preliminary AirNow days.

Validated part: AirData's daily_aqi_by_county_YYYY.zip, one per year, columns
  "State Name","county Name","State Code","County Code","Date","AQI","Category","Defining Parameter",
  "Defining Site","Number of Sites Reporting"
used as published (validated: true). See aqi.py for the county codes and what is dropped.

Preliminary part: AirNow's daily files for the last year (airnow.py), turned into county values the way
EPA does (validated: false). How the two are stitched:
  - a validated row always wins: a preliminary row is never published for a county and date AirData has;
  - AirData's prior years are complete (EPA's June release finishes the previous year), so a county's gap
    there means no valid data, and preliminary values must not fill it. Preliminary rows are kept only
    after the county's last validated date and after the end of the year before AirData's latest year.
That last rule follows AirNow's guidelines: its raw data "should never be substituted for validated data".
"""

from __future__ import annotations

import re

import polars as pl

from . import airnow
from .aqi import (
    AIRDATA,
    AIRDATA_PAGE,
    airdata_years,
    county_location,
    known_counties,
    normalize_zip,
    read_airdata_csv,
    us_counties,
)

PREFIX = "daily_aqi_by_county"
SERIES = "aqi"
COLUMNS = {
    "State Code": "state",
    "County Code": "county",
    "Date": "date",
    "AQI": "aqi",
    "Category": "category",
    "Defining Parameter": "defining_parameter",
    "Defining Site": "defining_site",
    "Number of Sites Reporting": "sites_reporting",
}
OUTPUT = [
    "date",
    "aqi",
    "category",
    "defining_parameter",
    "defining_site",
    "sites_reporting",
    "validated",
]
YEAR_NAME = re.compile(r"^airdata-(\d{4})$")


def resources(ctx) -> dict[str, str]:
    """Every yearly AirData file linked from the download page, plus the AirNow window."""
    page = ctx.get(AIRDATA_PAGE).decode("utf-8", "replace")
    res = {
        f"airdata-{y}": f"{AIRDATA}/{PREFIX}_{y}.zip"
        for y in airdata_years(page, PREFIX)
    }
    res.update(airnow.resources(ctx, ctx.now))
    return res


def normalize(name: str, body: bytes) -> bytes:
    return normalize_zip(name, body) if name.startswith("airdata-") else body


def validated(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name in sorted(raw):
        m = YEAR_NAME.match(name)
        if not m:
            continue
        df = read_airdata_csv(raw[name], COLUMNS, f"{PREFIX}_{m.group(1)}")
        off_year = df.filter(~pl.col("date").str.starts_with(m.group(1)))
        if len(off_year):
            raise ValueError(
                f"{PREFIX}_{m.group(1)}: {len(off_year)} rows dated outside the year (e.g. {off_year['date'][0]})"
            )
        frames.append(df)
    if not frames:
        raise ValueError("no AirData daily files in the raw inputs")
    df = pl.concat(frames).with_columns(fips=pl.col("state") + pl.col("county"))
    df = us_counties(df)
    return df.select(
        "fips",
        "date",
        pl.col("aqi").cast(pl.Int64, strict=True),
        "category",
        "defining_parameter",
        "defining_site",
        pl.col("sites_reporting").cast(pl.Int64, strict=True),
        pl.lit(True).alias("validated"),
    )


def stitch(valid: pl.DataFrame, prelim: pl.DataFrame) -> pl.DataFrame:
    """Validated rows plus the preliminary rows allowed after each county's validated record (see above)."""
    latest_year = int(valid["date"].max()[:4])
    year_floor = f"{latest_year - 1}-12-31"
    last_valid = valid.group_by("fips").agg(pl.col("date").max().alias("last_valid"))
    prelim = prelim.join(last_valid, on="fips", how="left").with_columns(
        cutoff=pl.max_horizontal(
            pl.col("last_valid").fill_null(year_floor), pl.lit(year_floor)
        )
    )
    prelim = prelim.filter(pl.col("date") > pl.col("cutoff")).drop(
        "last_valid", "cutoff"
    )
    # never a preliminary row where a validated one exists (implied by the cutoff; kept as a hard guarantee)
    prelim = prelim.join(valid.select("fips", "date"), on=["fips", "date"], how="anti")
    prelim = prelim.with_columns(validated=pl.lit(False))
    return pl.concat([valid, prelim.select(valid.columns)])


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    valid = validated(raw)
    prelim = airnow.county_days(raw)
    # AirNow ids are outside our control: a site id that names no known county is skipped rather than
    # blocking the validated history. AirData's codes are checked by the contract's location validation.
    prelim = prelim.filter(
        county_location(pl.col("fips")).is_in(list(known_counties()))
    )
    table = stitch(valid, prelim)
    return table.select(
        pl.lit(SERIES).alias("series"),
        county_location(pl.col("fips")).alias("location"),
        *OUTPUT,
    )
