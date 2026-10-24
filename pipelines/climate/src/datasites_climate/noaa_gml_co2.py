"""Monthly CO2 at Mauna Loa and the global marine-surface average, from NOAA GML.

Mauna Loa columns: year,month,decimal date,average,deseasonalized,ndays,sdev,unc
Global columns:    year,month,decimal,average,average_unc,trend,trend_unc
"""

from __future__ import annotations

import io

import polars as pl

RESOURCES = {
    "noaa-mlo": "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv",
    "world": "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_gl.csv",
}

# Before May 1974 the Mauna Loa record comes from Scripps, which provides no day counts or
# deviations; NOAA fills those columns with negative numbers that don't mean "interpolated".
NOAA_ERA_START = "1974-05"


def normalize(_name: str, body: bytes) -> bytes:
    """Drop the '# File Creation:' line, which changes on every regeneration even when the data doesn't."""
    return b"\n".join(line for line in body.split(b"\n") if not line.startswith(b"# File Creation"))


def _read(body: bytes) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(body), comment_prefix="#")
    return df.with_columns(
        pl.format("{}-{}", pl.col("year"), pl.col("month").cast(pl.Utf8).str.zfill(2)).alias("date")
    )


def _positive(col: str) -> pl.Expr:
    return pl.when(pl.col(col) >= 0).then(pl.col(col).cast(pl.Float64)).otherwise(None)


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    mlo = _read(raw["noaa-mlo"])
    noaa_era = pl.col("date") >= NOAA_ERA_START
    interpolated = pl.when(noaa_era).then(pl.col("sdev") < 0).otherwise(None).alias("interpolated")
    # NOAA reports 0.00 uncertainty for interpolated months; that isn't a real uncertainty.
    mlo_unc = pl.when(noaa_era & (pl.col("sdev") >= 0)).then(_positive("unc")).otherwise(None)

    mlo_average = mlo.select(
        pl.lit("average").alias("series"),
        pl.lit("noaa-mlo").alias("location"),
        "date",
        _positive("average").alias("value"),
        mlo_unc.alias("uncertainty"),
        interpolated,
    )
    mlo_trend = mlo.select(
        pl.lit("trend").alias("series"),
        pl.lit("noaa-mlo").alias("location"),
        "date",
        _positive("deseasonalized").alias("value"),
        pl.lit(None, dtype=pl.Float64).alias("uncertainty"),
        interpolated,
    )

    world = _read(raw["world"])
    no_flag = pl.lit(None, dtype=pl.Boolean).alias("interpolated")
    world_average = world.select(
        pl.lit("average").alias("series"),
        pl.lit("world").alias("location"),
        "date",
        _positive("average").alias("value"),
        _positive("average_unc").alias("uncertainty"),
        no_flag,
    )
    world_trend = world.select(
        pl.lit("trend").alias("series"),
        pl.lit("world").alias("location"),
        "date",
        _positive("trend").alias("value"),
        _positive("trend_unc").alias("uncertainty"),
        no_flag,
    )
    return pl.concat([mlo_average, mlo_trend, world_average, world_trend])
