"""Census Bureau American Community Survey 5-year estimates: median household income (table B19013)
for every county, state and the nation, one row per 5-year release.

Source: the Census Data API (https://api.census.gov/data/<year>/acs/acs5), which has required a key
on every data request since 2026-05-12 (read from CENSUS_API_KEY). Per release: one request each
for county:*, state:* and us:1, asking for NAME, B19013_001E (estimate) and B19013_001M (90 percent
margin of error). The newest release is found from the keyless dataset metadata
(/data/<year>/acs/acs5.json answers 404 until the release is out).

Values arrive as strings. Census encodes "no value" as sentinel numbers, documented at
https://www.census.gov/data/developers/data-sets/acs-1year/notes-on-acs-estimate-and-annotation-values.html;
they become nulls, with Census's annotation symbol kept alongside. The estimate is top-coded at
250,001 ("250,000+") and bottom-coded at 2,499 ("2,500-"); those keep their value and get the
annotation. Each release is in its own final year's inflation-adjusted dollars.

Geography follows each release: Connecticut's legacy counties through the 2017-2021 release, its
planning regions from 2018-2022; Alaska and South Dakota renames as they happened.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import polars as pl

from ._common import keyed_get, state_ids_by_fips

API = "https://api.census.gov/data/{year}/acs/acs5"
FIRST_YEAR = 2009  # the first ACS 5-year release (2005-2009)
ESTIMATE, MOE = "B19013_001E", "B19013_001M"
GEOGRAPHIES = {"county": "county:*", "state": "state:*", "us": "us:1"}

# Estimate/margin-of-error sentinel value -> annotation symbol (Census "Estimate and Annotation Values").
SENTINELS = {
    -666666666: "-",
    -999999999: "N",
    -888888888: "(X)",
    -222222222: "**",
    -333333333: "***",
    -555555555: "*****",
}
OPEN_ENDED = {250001: "250,000+", 2499: "2,500-"}  # B19013's top and bottom codes


def _released(ctx: Any, year: int) -> bool:
    try:
        ctx.get(API.format(year=year) + ".json")
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 404:
            return False
        raise RuntimeError(f"Census ACS {year} metadata: HTTP {err.response.status_code}") from None
    return True


def latest_release(ctx: Any) -> int:
    year = ctx.now.year - 1
    while year >= FIRST_YEAR:
        if _released(ctx, year):
            return year
        year -= 1
    raise RuntimeError("no ACS 5-year release found on the Census Data API")


def collect(ctx: Any) -> dict[str, bytes]:
    key = ctx.env("CENSUS_API_KEY")
    out: dict[str, bytes] = {}
    for year in range(FIRST_YEAR, latest_release(ctx) + 1):
        for level, predicate in GEOGRAPHIES.items():
            what = f"Census ACS {year} 5-year {level}"
            params = {"get": f"NAME,{ESTIMATE},{MOE}", "for": predicate, "key": key}
            body = keyed_get(ctx, API.format(year=year), params, what)
            # A rejected key redirects to an HTML page instead of returning JSON.
            if not body.lstrip().startswith(b"["):
                raise RuntimeError(f"{what}: the Census API did not return data (is CENSUS_API_KEY valid?)")
            out[f"{year}-{level}"] = body
    return out


def read_response(body: bytes, estimate: str, moe: str | None = None) -> pl.DataFrame:
    """A Census Data API response (a JSON array whose first row names the columns) -> one row per
    geography: location id, name, estimate and margin of error with their annotations."""
    table = json.loads(body)
    if not isinstance(table, list) or not table or not isinstance(table[0], list):
        raise ValueError("Census response is not a JSON table")
    header, rows = table[0], table[1:]
    if estimate not in header or (moe is not None and moe not in header):
        raise ValueError(f"Census response has columns {header}, expected {estimate} and {moe}")
    df = pl.DataFrame(rows, schema=[(name, pl.String) for name in header], orient="row")
    if "county" in df.columns:
        location = pl.lit("us-county-") + pl.col("state") + pl.col("county")
    elif "state" in df.columns:
        location = pl.col("state").replace_strict(state_ids_by_fips(), default=None)
    elif "us" in df.columns:
        location = pl.lit("usa")
    else:
        raise ValueError(f"Census response has no county, state or us column: {header}")

    def value(col: str) -> pl.Expr:
        number = pl.col(col).cast(pl.Float64, strict=False)
        sentinel = number.is_in([float(code) for code in SENTINELS])
        return pl.when(sentinel).then(None).otherwise(number).round(0).cast(pl.Int64)

    def annotation(col: str, open_ended: bool) -> pl.Expr:
        number = pl.col(col).cast(pl.Float64, strict=False).cast(pl.Int64, strict=False)
        codes = {**SENTINELS, **(OPEN_ENDED if open_ended else {})}
        return number.replace_strict(codes, default=None, return_dtype=pl.String)

    return df.select(
        location.alias("location"),
        pl.col("NAME").alias("location_name"),
        value(estimate).alias("estimate"),
        annotation(estimate, True).alias("estimate_annotation"),
        (value(moe) if moe else pl.lit(None, dtype=pl.Int64)).alias("moe"),
        (annotation(moe, False) if moe else pl.lit(None, dtype=pl.String)).alias("moe_annotation"),
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name in sorted(raw):
        year, _level = name.split("-", 1)
        frames.append(read_response(raw[name], ESTIMATE, MOE).with_columns(pl.lit(year).alias("date")))
    out = pl.concat(frames)
    if out["location"].null_count():
        raise ValueError("Census state rows with a FIPS code missing from the registry")
    return out.select(
        pl.lit("median-household-income").alias("series"),
        "location",
        "date",
        pl.col("estimate").alias("median_household_income"),
        pl.col("moe").alias("margin_of_error"),
        "estimate_annotation",
        "moe_annotation",
        "location_name",
    )
