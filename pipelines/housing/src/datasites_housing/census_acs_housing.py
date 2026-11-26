"""Census Bureau American Community Survey 5-year estimates: what housing costs, for every county, state
and the nation, one row per 5-year release.

Series, each with its 90 percent margin of error:
- median-gross-rent: median gross rent (contract rent plus utilities), table B25064, in dollars;
- median-home-value: median value of owner-occupied homes, table B25077, in dollars;
- rent-burdened-share: renter households paying 30 percent or more of their income in gross rent, as a
  percent of renter households for whom the ratio is computed (table B25070: lines 7-10 over line 1
  minus line 11, "not computed");
- owner-occupied-share: owner-occupied homes as a percent of occupied homes (table B25003: line 2 over
  line 1).

The two shares are derived here, not published by Census. Their margins of error use the Census Bureau's
formula for a derived proportion (Understanding and Using ACS Data, 2020, chapter 8): the margin of a
sum is the root of the summed squared margins, and for a proportion p = X / Y,
    MOE(p) = sqrt(MOE(X)^2 - p^2 * MOE(Y)^2) / Y,
with + instead of - when the term under the root would be negative.

Source: the Census Data API (https://api.census.gov/data/<year>/acs/acs5), keyed (CENSUS_API_KEY). Per
release: one request each for county:*, state:* and us:1. The newest release is found from the keyless
dataset metadata, which answers 404 until a release is out. Census's "no value" sentinels become nulls;
the medians' estimate and margin annotations (for example "2,000+" when the median is above the table's
top bracket, or "-" when there were too few cases) come from the API's own EA and MA variables. Each
release is in its own final year's inflation-adjusted dollars, and geography follows each release.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import polars as pl

from datasites_core import geo

API = "https://api.census.gov/data/{year}/acs/acs5"
FIRST_YEAR = 2009  # the first ACS 5-year release (2005-2009)
GEOGRAPHIES = {"county": "county:*", "state": "state:*", "us": "us:1"}
MEDIANS = {"median-gross-rent": "B25064_001", "median-home-value": "B25077_001"}
BURDENED = [f"B25070_{line:03d}" for line in (7, 8, 9, 10)]  # 30.0-34.9, 35.0-39.9, 40.0-49.9, 50+ percent
RENTERS, NOT_COMPUTED = "B25070_001", "B25070_011"
OCCUPIED, OWNERS = "B25003_001", "B25003_002"
COUNTS = [*BURDENED, RENTERS, NOT_COMPUTED, OCCUPIED, OWNERS]
SERIES = [*MEDIANS, "rent-burdened-share", "owner-occupied-share"]

# Estimate/margin-of-error sentinel value -> annotation symbol (Census "Estimate and Annotation Values").
SENTINELS = {
    -666666666: "-",
    -999999999: "N",
    -888888888: "(X)",
    -222222222: "**",
    -333333333: "***",
    -555555555: "*****",
}


def variables() -> list[str]:
    out = ["NAME"]
    for var in MEDIANS.values():
        out += [f"{var}E", f"{var}M", f"{var}EA", f"{var}MA"]
    for var in COUNTS:
        out += [f"{var}E", f"{var}M"]
    return out


def state_ids_by_fips() -> dict[str, str]:
    return {loc["fips"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}


def keyed_get(ctx: Any, url: str, params: dict[str, Any], what: str) -> bytes:
    """ctx.get for an API that takes a secret in the query string, re-raising without the query string so
    the key can never reach the run log or the dataset's state file."""
    try:
        return ctx.get(url, params=params)
    except httpx.HTTPStatusError as err:
        raise RuntimeError(f"{what}: HTTP {err.response.status_code} from {url}") from None
    except httpx.HTTPError as err:
        raise RuntimeError(f"{what}: {type(err).__name__} requesting {url}") from None


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
            what = f"Census ACS {year} 5-year {level} housing"
            body = keyed_get(ctx, API.format(year=year), {"get": ",".join(variables()), "for": predicate, "key": key}, what)
            # A rejected key redirects to an HTML page instead of returning JSON.
            if not body.lstrip().startswith(b"["):
                raise RuntimeError(f"{what}: the Census API did not return data (is CENSUS_API_KEY valid?)")
            out[f"{year}-{level}"] = body
    return out


def _table(body: bytes) -> pl.DataFrame:
    table = json.loads(body)
    if not isinstance(table, list) or not table or not isinstance(table[0], list):
        raise ValueError("Census response is not a JSON table")
    header, rows = table[0], table[1:]
    missing = sorted(set(variables()) - set(header))
    if missing:
        raise ValueError(f"Census response is missing {missing}")
    return pl.DataFrame(rows, schema=[(name, pl.String) for name in header], orient="row")


def _location(df: pl.DataFrame) -> pl.Expr:
    if "county" in df.columns:
        return pl.lit("us-county-") + pl.col("state") + pl.col("county")
    if "state" in df.columns:
        return pl.col("state").replace_strict(state_ids_by_fips(), default=None)
    if "us" in df.columns:
        return pl.lit("usa")
    raise ValueError(f"Census response has no county, state or us column: {df.columns}")


def _number(col: str) -> pl.Expr:
    """A count or median as a float; Census's sentinels become null."""
    number = pl.col(col).cast(pl.Float64, strict=False)
    return pl.when(number.is_in([float(code) for code in SENTINELS])).then(None).otherwise(number)


def _sentinel(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Float64, strict=False).cast(pl.Int64, strict=False).replace_strict(SENTINELS, default=None, return_dtype=pl.String)


def _annotation(estimate_col: str, annotation_col: str) -> pl.Expr:
    """Census's own annotation (EA/MA), else the symbol of a sentinel value."""
    own = pl.col(annotation_col).str.strip_chars()
    return pl.when(own.is_not_null() & (own != "")).then(own).otherwise(_sentinel(estimate_col))


def _share(numerator: pl.Expr, numerator_moe: pl.Expr, denominator: pl.Expr, denominator_moe: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    """A derived proportion in percent and its margin of error (Census's formula; + when - goes negative)."""
    p = numerator / denominator
    minus = numerator_moe**2 - p**2 * denominator_moe**2
    moe = pl.when(minus >= 0).then(minus.sqrt()).otherwise((numerator_moe**2 + p**2 * denominator_moe**2).sqrt()) / denominator
    defined = denominator > 0
    return (
        pl.when(defined).then((100 * p).round(1)).otherwise(None),
        pl.when(defined).then((100 * moe).round(1)).otherwise(None),
    )


def read_response(body: bytes, year: str) -> pl.DataFrame:
    """One Census response -> one row per geography and series."""
    df = _table(body)
    base = [_location(df).alias("location"), pl.col("NAME").alias("location_name"), pl.lit(year).alias("date")]
    frames = []
    for series, var in MEDIANS.items():
        frames.append(
            df.select(
                pl.lit(series).alias("series"),
                *base,
                _number(f"{var}E").round(0).alias("value"),
                _number(f"{var}M").round(0).alias("margin_of_error"),
                _annotation(f"{var}E", f"{var}EA").alias("estimate_annotation"),
                _annotation(f"{var}M", f"{var}MA").alias("moe_annotation"),
            )
        )

    burdened = pl.sum_horizontal(_number(f"{v}E") for v in BURDENED)
    burdened_moe = pl.sum_horizontal(_number(f"{v}M") ** 2 for v in BURDENED).sqrt()
    any_burdened_missing = pl.any_horizontal(_number(f"{v}E").is_null() | _number(f"{v}M").is_null() for v in BURDENED)
    computed = _number(f"{RENTERS}E") - _number(f"{NOT_COMPUTED}E")
    computed_moe = (_number(f"{RENTERS}M") ** 2 + _number(f"{NOT_COMPUTED}M") ** 2).sqrt()
    share, share_moe = _share(burdened, burdened_moe, computed, computed_moe)
    frames.append(
        df.select(
            pl.lit("rent-burdened-share").alias("series"),
            *base,
            pl.when(any_burdened_missing).then(None).otherwise(share).alias("value"),
            pl.when(any_burdened_missing).then(None).otherwise(share_moe).alias("margin_of_error"),
            pl.lit(None, dtype=pl.String).alias("estimate_annotation"),
            pl.lit(None, dtype=pl.String).alias("moe_annotation"),
        )
    )

    owners, owners_moe = _share(_number(f"{OWNERS}E"), _number(f"{OWNERS}M"), _number(f"{OCCUPIED}E"), _number(f"{OCCUPIED}M"))
    frames.append(
        df.select(
            pl.lit("owner-occupied-share").alias("series"),
            *base,
            owners.alias("value"),
            owners_moe.alias("margin_of_error"),
            pl.lit(None, dtype=pl.String).alias("estimate_annotation"),
            pl.lit(None, dtype=pl.String).alias("moe_annotation"),
        )
    )
    return pl.concat(frames)


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = [read_response(raw[name], name.split("-", 1)[0]) for name in sorted(raw)]
    out = pl.concat(frames)
    if out["location"].null_count():
        raise ValueError("Census state rows with a FIPS code missing from the registry")
    return out.select("series", "location", "date", "value", "margin_of_error", "estimate_annotation", "moe_annotation", "location_name")
