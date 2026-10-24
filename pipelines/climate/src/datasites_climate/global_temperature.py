"""Monthly global and hemispheric surface temperature anomalies from NASA GISTEMP v4 and Met Office HadCRUT5.

GISTEMP tables (GLB/NH/SH.Ts+dSST.csv): a title line, then Year,Jan..Dec,J-D,D-N,DJF,MAM,JJA,SON in deg C
  relative to 1951-1980; '***' marks months not yet available; values are written like '-.19'.
HadCRUT5 analysis summary series: Time (YYYY-MM),Anomaly (deg C),Lower confidence limit (2.5%),
  Upper confidence limit (97.5%), relative to 1961-1990.

The HadCRUT version is part of the URL and of the licence acknowledgement in the contract, so a new version
is a reviewed change: once the Met Office moves on, these files stop updating and the SLA flags it.
"""

from __future__ import annotations

import io

import polars as pl

GISTEMP = "https://data.giss.nasa.gov/gistemp/tabledata_v4/{region}.Ts+dSST.csv"
HADCRUT_VERSION = "5.2.0.0"
HADCRUT = (
    f"https://www.metoffice.gov.uk/hadobs/hadcrut5/data/HadCRUT.{HADCRUT_VERSION}/analysis/diagnostics/"
    f"HadCRUT.{HADCRUT_VERSION}.analysis.summary_series.{{region}}.monthly.csv"
)

REGIONS = {  # location id -> (GISTEMP table, HadCRUT region)
    "world": ("GLB", "global"),
    "nh": ("NH", "northern_hemisphere"),
    "sh": ("SH", "southern_hemisphere"),
}
BASELINES = {"gistemp": "1951-1980", "hadcrut5": "1961-1990"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HADCRUT_COLUMNS = ["Time", "Anomaly (deg C)", "Lower confidence limit (2.5%)", "Upper confidence limit (97.5%)"]

RESOURCES = {
    **{f"gistemp-{loc}": GISTEMP.format(region=g) for loc, (g, _h) in REGIONS.items()},
    **{f"hadcrut5-{loc}": HADCRUT.format(region=h) for loc, (_g, h) in REGIONS.items()},
}


def _drop_unobserved(frame: pl.DataFrame) -> pl.DataFrame:
    """Months after the last reported one are placeholders, not gaps."""
    last = frame.filter(pl.col("value").is_not_null())["date"].max()
    return frame.filter(pl.col("date") <= last) if last is not None else frame.clear()


def gistemp(body: bytes, location: str) -> pl.DataFrame:
    text = body.decode("utf-8")
    title, _, table = text.partition("\n")
    if not title.startswith("Land-Ocean"):
        raise ValueError(f"GISTEMP {location}: unexpected title line {title[:60]!r}")
    df = pl.read_csv(io.StringIO(table), null_values=["***"], infer_schema=False)
    missing = [c for c in ["Year", *MONTHS] if c not in df.columns]
    if missing:
        raise ValueError(f"GISTEMP {location}: columns {missing} missing (header is {df.columns})")
    long = (
        df.select("Year", *MONTHS)
        .unpivot(index="Year", variable_name="month", value_name="text")
        .select(
            pl.format(
                "{}-{}",
                pl.col("Year").cast(pl.Int32, strict=True),
                pl.col("month").replace_strict({m: f"{i + 1:02d}" for i, m in enumerate(MONTHS)}),
            ).alias("date"),
            pl.col("text").str.strip_chars().cast(pl.Float64, strict=True).alias("value"),
        )
        .sort("date")
    )
    return _drop_unobserved(long).select(
        pl.lit("gistemp").alias("series"),
        pl.lit(location).alias("location"),
        "date",
        "value",
        pl.lit(None, dtype=pl.Float64).alias("lower"),
        pl.lit(None, dtype=pl.Float64).alias("upper"),
        pl.lit(BASELINES["gistemp"]).alias("baseline"),
    )


def hadcrut(body: bytes, location: str) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(body), infer_schema=False)
    if df.columns != HADCRUT_COLUMNS:
        raise ValueError(f"HadCRUT5 {location}: header is {df.columns}, expected {HADCRUT_COLUMNS}")
    number = lambda col: pl.col(col).str.strip_chars().cast(pl.Float64, strict=True).fill_nan(None)  # noqa: E731
    long = df.select(
        pl.col("Time").alias("date"),
        number("Anomaly (deg C)").alias("value"),
        number("Lower confidence limit (2.5%)").alias("lower"),
        number("Upper confidence limit (97.5%)").alias("upper"),
    ).sort("date")
    bad = long.filter(~pl.col("date").str.contains(r"^\d{4}-(0[1-9]|1[0-2])$"))
    if len(bad):
        raise ValueError(f"HadCRUT5 {location}: unexpected Time value {bad['date'][0]!r}")
    return _drop_unobserved(long).select(
        pl.lit("hadcrut5").alias("series"),
        pl.lit(location).alias("location"),
        "date",
        "value",
        "lower",
        "upper",
        pl.lit(BASELINES["hadcrut5"]).alias("baseline"),
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return pl.concat(
        [gistemp(raw[f"gistemp-{loc}"], loc) for loc in REGIONS] + [hadcrut(raw[f"hadcrut5-{loc}"], loc) for loc in REGIONS]
    )
