"""Global monthly methane and nitrous oxide from NOAA GML's marine-surface network.

Both files have the co2_mm_gl.csv layout: year,month,decimal,average,average_unc,trend,trend_unc (ppb).
NOAA writes -9.99 (its header says -9.9) where an uncertainty has not been calculated yet, which is
the case for the most recent months; those become null.
"""

from __future__ import annotations

import io

import polars as pl

RESOURCES = {
    "ch4": "https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_mm_gl.csv",
    "n2o": "https://gml.noaa.gov/webdata/ccgg/trends/n2o/n2o_mm_gl.csv",
}
COLUMNS = ["year", "month", "decimal", "average", "average_unc", "trend", "trend_unc"]


def normalize(_name: str, body: bytes) -> bytes:
    """Drop the '# File Creation:' line, which changes on every regeneration even when the data doesn't."""
    return b"\n".join(line for line in body.split(b"\n") if not line.startswith(b"# File Creation"))


def _positive(col: str) -> pl.Expr:
    return pl.when(pl.col(col) >= 0).then(pl.col(col).cast(pl.Float64)).otherwise(None)


def _read(gas: str, body: bytes) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(body), comment_prefix="#")
    if df.columns != COLUMNS:
        raise ValueError(f"{gas}: header is {df.columns}, expected {COLUMNS}")
    df = df.filter(pl.any_horizontal(pl.all().is_not_null()))  # the blank line after the header reads as a null row
    if df["year"].null_count() or df["month"].null_count():
        raise ValueError(f"{gas}: rows without a year or month")
    df = df.with_columns(pl.format("{}-{}", pl.col("year"), pl.col("month").cast(pl.Utf8).str.zfill(2)).alias("date"))
    average = df.select(
        pl.lit(gas).alias("series"),
        pl.lit("world").alias("location"),
        "date",
        _positive("average").alias("value"),
        _positive("average_unc").alias("uncertainty"),
    )
    trend = df.select(
        pl.lit(f"{gas}-trend").alias("series"),
        pl.lit("world").alias("location"),
        "date",
        _positive("trend").alias("value"),
        _positive("trend_unc").alias("uncertainty"),
    )
    return pl.concat([average, trend])


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return pl.concat([_read(gas, raw[gas]) for gas in RESOURCES])
