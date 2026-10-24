"""waterdata/drought-county: weekly US Drought Monitor categories for every county, over the last 400 days.

Source: the USDM data services (https://usdmdataservices.unl.edu/api/), one request per state:
  CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent?aoi=<state>&startdate=M/D/YYYY&enddate=...&statisticsType=2
CSV columns: MapDate,FIPS,County,State,None,D0,D1,D2,D3,D4,ValidStart,ValidEnd,StatisticFormatID

statisticsType=2 is USDM's "categorical" format: each column is the percent of the county's area in exactly
that category, so none + D0 + ... + D4 = 100. (The "traditional" format is cumulative: its D1 means D1-D4.)
A map is dated by its valid date (MapDate, a Tuesday); USDM releases it on the Thursday after.

The window matches streamflow-daily. A full 2000-to-date history would be about 400 MB a day from a
university server that sends no ETags, so it isn't re-fetched daily.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

import polars as pl

from datasites_core import geo

URL = "https://usdmdataservices.unl.edu/api/CountyStatistics/GetDroughtSeverityStatisticsByAreaPercent"
WINDOW_DAYS = 400
CATEGORICAL = "2"
COLUMNS = ["MapDate", "FIPS", "County", "State", "None", "D0", "D1", "D2", "D3", "D4", "ValidStart", "ValidEnd", "StatisticFormatID"]
CATEGORIES = {"None": "none", "D0": "d0", "D1": "d1", "D2": "d2", "D3": "d3", "D4": "d4"}


def states() -> list[str]:
    """USPS codes of every state or equivalent that has counties in the registry: 50 states, DC and PR."""
    places = geo.load()
    parents = {p["parent"] for p in places.values() if p["level"] == "us-county"}
    return sorted(places[state]["usps"] for state in parents)


def _us_date(day: date) -> str:
    return f"{day.month}/{day.day}/{day.year}"


def resources(ctx: Any) -> dict[str, str]:
    end = ctx.now.date()
    start = end - timedelta(days=WINDOW_DAYS - 1)
    query = f"startdate={_us_date(start)}&enddate={_us_date(end)}&statisticsType={CATEGORICAL}"
    return {f"counties-{usps.lower()}": f"{URL}?aoi={usps}&{query}" for usps in states()}


def _read(name: str, body: bytes) -> pl.DataFrame:
    # An unknown or uncovered area comes back as 200 with an empty body; for a state that means a broken source.
    if not body.strip():
        raise ValueError(f"{name}: USDM returned no county statistics")
    table = pl.read_csv(io.BytesIO(body), infer_schema=False)
    if table.columns != COLUMNS:
        raise ValueError(f"{name}: unexpected USDM columns {table.columns}")
    formats = set(table["StatisticFormatID"].unique())
    if formats != {CATEGORICAL}:
        raise ValueError(f"{name}: expected categorical statistics (format {CATEGORICAL}), got {sorted(formats)}")
    return table


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    table = pl.concat([_read(name, raw[name]) for name in sorted(raw)])
    bad = table.filter(~pl.col("FIPS").str.contains(r"^\d{5}$") | ~pl.col("MapDate").str.contains(r"^\d{8}$"))
    if len(bad):
        raise ValueError(f"malformed USDM rows, e.g. {bad.row(0)}")
    return table.select(
        pl.lit("area").alias("series"),
        ("us-county-" + pl.col("FIPS")).alias("location"),
        pl.col("MapDate").str.strptime(pl.Date, "%Y%m%d").cast(pl.Utf8).alias("date"),
        *[pl.col(src).cast(pl.Float64, strict=True).alias(dst) for src, dst in CATEGORIES.items()],
    )
