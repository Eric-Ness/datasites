"""waterdata/streamflow-daily: daily mean discharge at every USGS stream gage over the last 400 days.

Source: the `daily` collection of the USGS Water Data OGC APIs (parameter 00060, statistic 00003), CSV.
About 9,000 gages report each day, so the window is asked for in 5-day blocks of about 45,000 rows, each
under the 50,000-row page limit. Blocks are aligned to fixed dates, so a block's request (and, once its
values stop being revised, its bytes) is the same from one day to the next. A block that fills a whole page
is split in half and asked for again, so growth in the network can never silently truncate a block.

What a row means:
  value        the daily mean in ft^3/s; null when USGS publishes the day without a value (ice, equipment
               failure, seasonal operation, ...). Negative values are real: tidal and regulated channels
               can flow upstream.
  provisional  true unless USGS has approved the value. Provisional data are revised for months.
  estimated    the ESTIMATED qualifier.
  ice          the ICE qualifier (ice-affected; the value, if any, is usually estimated).
  qualifiers   every qualifier code exactly as USGS lists it, e.g. "BACKWATER,ESTIMATED".
Days with no record at all are absent: they are never filled in.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl

from . import usgs

BLOCK_DAYS = 5
EPOCH = date(2000, 1, 1)  # block boundaries fall every BLOCK_DAYS days from here
DAILY_PROPERTIES = "time_series_id,time,value,approval_status,qualifier"
DAILY_COLUMNS = ["x", "y", *DAILY_PROPERTIES.split(",")]


def blocks(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive (first, last) date ranges covering start..end, cut at fixed BLOCK_DAYS boundaries."""
    out = []
    first = start
    while first <= end:
        offset = (first - EPOCH).days % BLOCK_DAYS
        last = min(first + timedelta(days=BLOCK_DAYS - 1 - offset), end)
        out.append((first, last))
        first = last + timedelta(days=1)
    return out


def fetch_block(ctx: Any, first: date, last: date) -> dict[str, bytes]:
    body = usgs.get_csv(
        ctx,
        "daily",
        {
            "parameter_code": usgs.DISCHARGE,
            "statistic_id": usgs.DAILY_MEAN,
            "time": f"{first.isoformat()}/{last.isoformat()}",
            "properties": DAILY_PROPERTIES,
        },
    )
    if len(usgs.read_csv(body, DAILY_COLUMNS)) >= usgs.PAGE_LIMIT:
        if first == last:
            raise RuntimeError(f"daily values for {first} alone fill a {usgs.PAGE_LIMIT}-row page")
        middle = first + timedelta(days=(last - first).days // 2)
        return {**fetch_block(ctx, first, middle), **fetch_block(ctx, middle + timedelta(days=1), last)}
    return {f"daily-{first.isoformat()}": body}


def collect(ctx: Any) -> dict[str, bytes]:
    start, end = usgs.window(ctx.now)
    raw = {"query": usgs.query_entry(start, end, block_days=BLOCK_DAYS), **usgs.collect_gages(ctx, start)}
    for first, last in blocks(start, end):
        raw.update(fetch_block(ctx, first, last))
    return raw


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    query = usgs.read_query(raw)
    sites = usgs.gages(raw).select("time_series_id", pl.col("site_id").alias("location"), pl.col("name").alias("location_name"))
    daily = pl.concat([usgs.read_csv(raw[name], DAILY_COLUMNS) for name in sorted(raw) if name.startswith("daily-")])
    codes = pl.col("qualifier").str.split(",")
    return (
        daily.join(sites, on="time_series_id", how="inner")
        .filter(pl.col("time").is_between(pl.lit(query["window_start"]), pl.lit(query["window_end"])))
        .select(
            pl.lit("discharge").alias("series"),
            "location",
            "location_name",
            pl.col("time").alias("date"),
            pl.col("value").cast(pl.Float64, strict=True).alias("value"),
            pl.col("approval_status").fill_null("").ne("Approved").alias("provisional"),
            codes.list.contains("ESTIMATED").fill_null(False).alias("estimated"),
            codes.list.contains("ICE").fill_null(False).alias("ice"),
            pl.col("qualifier").alias("qualifiers"),
        )
    )
