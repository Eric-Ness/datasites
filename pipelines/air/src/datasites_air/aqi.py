"""Shared pieces of the airdata pipelines: AQI categories, EPA AirData files, county ids.

EPA AirData (https://aqs.epa.gov/aqsweb/airdata/download_files.html) publishes pre-generated zips, one per
year, each holding one CSV. AQS identifies a county by its two-digit state code and three-digit county
code, which are FIPS codes, so "State Code" + "County Code" is the county's FIPS code. Three things in the
files are not US counties and are dropped on purpose:
  - state code "80" (Country Of Mexico) and "CC" (Canada): border monitors AQS stores for the US;
  - 23901, "MOBILE MONITORS" in Maine (1993-2004): a pseudo-county for mobile monitors, not a place.
AQS still files Connecticut under its eight former counties (09001-09015), not the 2022 planning regions,
and keeps a few retired Alaska boroughs and the island areas (Guam, US Virgin Islands) that the Census
gazetteer behind the geo registry leaves out. The contracts list those as explicit legacy locations, with
their real FIPS codes; nothing is remapped, because a monitor cannot be reassigned to a planning region
without its coordinates and AQS itself has not done so.
"""

from __future__ import annotations

import io
import re
import zipfile
from functools import cache

import polars as pl

AIRDATA = "https://aqs.epa.gov/aqsweb/airdata"
AIRDATA_PAGE = f"{AIRDATA}/download_files.html"
FIRST_YEAR = 1980

NON_US_STATE_CODES = ("80", "CC")  # Country Of Mexico, Canada
PSEUDO_COUNTIES = ("23901",)  # Maine "MOBILE MONITORS"

# EPA's AQI categories (40 CFR Part 58 Appendix G, Table 2; the 2024 revision merged 301-500 into one
# Hazardous category). AirData applies the current breakpoints to every year, and so does AirNow.
CATEGORIES = (
    (0, 50, "Good"),
    (51, 100, "Moderate"),
    (101, 150, "Unhealthy for Sensitive Groups"),
    (151, 200, "Unhealthy"),
    (201, 300, "Very Unhealthy"),
    (301, None, "Hazardous"),
)
CATEGORY_NAMES = tuple(name for _lo, _hi, name in CATEGORIES)


def category(aqi: int) -> str:
    """The AQI category of one value (AQI values above 500 stay Hazardous)."""
    if aqi < 0:
        raise ValueError(f"AQI {aqi} is negative")
    for lo, hi, name in CATEGORIES:
        if hi is None or aqi <= hi:
            return name
    raise AssertionError("unreachable")


def category_expr(column: str) -> pl.Expr:
    """category() for a whole column."""
    expr = pl.when(pl.col(column) <= CATEGORIES[0][1]).then(pl.lit(CATEGORIES[0][2]))
    for _lo, hi, name in CATEGORIES[1:-1]:
        expr = expr.when(pl.col(column) <= hi).then(pl.lit(name))
    return expr.otherwise(pl.lit(CATEGORIES[-1][2]))


def county_location(fips: pl.Expr) -> pl.Expr:
    return pl.lit("us-county-") + fips


def airdata_years(page: str, prefix: str) -> list[int]:
    """Years linked from the AirData download page for files named <prefix>_YYYY.zip."""
    years = sorted(
        {
            int(y)
            for y in re.findall(rf'href="{re.escape(prefix)}_(\d{{4}})\.zip"', page)
        }
    )
    expected = set(range(FIRST_YEAR, max(years, default=FIRST_YEAR) + 1))
    if not years or years[0] != FIRST_YEAR or set(years) != expected:
        raise ValueError(
            f"{AIRDATA_PAGE}: expected {prefix}_YYYY.zip links for every year from {FIRST_YEAR}, found {years}"
        )
    return years


def unzip_csv(body: bytes) -> bytes:
    """The single CSV inside an AirData zip."""
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(
                f"expected one CSV in the AirData zip, found {zf.namelist()}"
            )
        return zf.read(names[0])


def read_airdata_csv(body: bytes, columns: dict[str, str], where: str) -> pl.DataFrame:
    """Reads an AirData CSV (zipped or not), checks its header and returns `columns` renamed, all as text."""
    csv = unzip_csv(body) if body[:2] == b"PK" else body
    header = pl.read_csv(io.BytesIO(csv), n_rows=0).columns
    missing = [c for c in columns if c not in header]
    if missing:
        raise ValueError(
            f"{where}: columns {missing} not found; the header is {header}"
        )
    df = pl.read_csv(io.BytesIO(csv), columns=list(columns), infer_schema=False)
    return df.rename(columns)


def normalize_zip(_name: str, body: bytes) -> bytes:
    """Change detection on the CSV inside, so a re-zipped but identical file doesn't trigger a rebuild."""
    return unzip_csv(body) if body[:2] == b"PK" else body


@cache
def known_counties() -> frozenset[str]:
    """County location ids the airdata contracts accept: the geo registry plus the explicit legacy ones."""
    from datasites_core.contracts import load_contract

    return frozenset(load_contract("airdata/county-aqi-daily").locations.names())


def us_counties(df: pl.DataFrame, fips: str = "fips") -> pl.DataFrame:
    """Drops the rows that are not US counties (see the module docstring)."""
    return df.filter(
        ~pl.col(fips).str.slice(0, 2).is_in(NON_US_STATE_CODES)
        & ~pl.col(fips).is_in(PSEUDO_COUNTIES)
    )
