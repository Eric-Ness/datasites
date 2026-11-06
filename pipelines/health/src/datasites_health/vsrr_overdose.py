"""Provisional drug overdose deaths from CDC NCHS's Vital Statistics Rapid Release (VSRR).

Two data.cdc.gov tables, both counts of deaths in the 12 months ending with each month:
- xkb8-kh2a, by state (2015-): State, Year, Month, Period, Indicator, Data Value, Percent Complete,
  Percent Pending Investigation, State Name, Footnote, Footnote Symbol, Predicted Value.
  Indicators are drug groups (by ICD-10 multiple-cause code), all overdose deaths, deaths from all causes and
  the percent of overdose death records that name a drug.
- gb4e-yj24, by county (2020-): Data as of, Year, Month, ST_ABBREV, STATE_NAME, COUNTYNAME, FIPS, STATEFIPS,
  COUNTYFIPS, CODE2013, Provisional Drug Overdose Deaths, Footnote, Percentage Of Records Pending
  Investigation, HistoricalDataCompletenessNote, MonthEndingDate, Start Date, End Date.

What the transforms change:
- New York: VSRR reports New York City ("YC") apart from the rest of the state ("NY"). us-ny adds them up (its
  percent complete and pending are null), so us-ny is the whole state, as everywhere else on the sites.
- Footnotes become flags. "Data suppressed (<10)" and "low data quality" leave the count null (CDC withholds
  it); "suppressed" is true for the first. "Underreported due to incomplete data" sets "underreported".
- The county table lists two Virginia cities a second time under the counties they merged into (Bedford,
  Clifton Forge), with the same figures; one row is kept. Different figures would stop the run.
"""

from __future__ import annotations

import io

import polars as pl

from datasites_core import geo

SOCRATA = "https://data.cdc.gov/api/views"
STATE_ID = "xkb8-kh2a"
COUNTY_ID = "gb4e-yj24"
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1
)}  # fmt: skip
# VSRR indicator -> series id. "Percent with drugs specified" becomes a column instead.
INDICATORS = {
    "Number of Drug Overdose Deaths": "all-drugs",
    "Opioids (T40.0-T40.4,T40.6)": "opioids",
    "Synthetic opioids, excl. methadone (T40.4)": "synthetic-opioids",
    "Heroin (T40.1)": "heroin",
    "Natural & semi-synthetic opioids (T40.2)": "natural-opioids",
    "Methadone (T40.3)": "methadone",
    "Natural & semi-synthetic opioids, incl. methadone (T40.2, T40.3)": "natural-opioids-methadone",
    "Natural, semi-synthetic, & synthetic opioids, incl. methadone (T40.2-T40.4)": "all-opioids-excl-heroin",
    "Cocaine (T40.5)": "cocaine",
    "Psychostimulants with abuse potential (T43.6)": "psychostimulants",
    "Number of Deaths": "all-causes",
}
SPECIFIED = "Percent with drugs specified"
STATE_COLUMNS = [
    "State", "Year", "Month", "Period", "Indicator", "Data Value", "Percent Complete", "Percent Pending Investigation",
    "State Name", "Footnote", "Footnote Symbol", "Predicted Value",
]  # fmt: skip
COUNTY_COLUMNS = [
    "Data as of", "Year", "Month", "ST_ABBREV", "STATE_NAME", "COUNTYNAME", "FIPS", "STATEFIPS", "COUNTYFIPS", "CODE2013",
    "Provisional Drug Overdose Deaths", "Footnote", "Percentage Of Records Pending Investigation",
    "HistoricalDataCompletenessNote", "MonthEndingDate", "Start Date", "End Date",
]  # fmt: skip


def csv_url(dataset_id: str) -> str:
    """The whole table as CSV. data.cdc.gov answers it with an ETag and Last-Modified, so reruns cost a 304."""
    return f"{SOCRATA}/{dataset_id}/rows.csv?accessType=DOWNLOAD"


def read(body: bytes, columns: list[str], what: str) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(body), infer_schema_length=0)
    if df.columns != columns:
        missing = [c for c in columns if c not in df.columns]
        extra = [c for c in df.columns if c not in columns]
        raise ValueError(f"{what}: columns changed (missing {missing}, unexpected {extra})")
    return df.with_columns(pl.col(c).str.strip_chars() for c in df.columns)


def number(df: pl.DataFrame, column: str, what: str) -> pl.Expr:
    """A numeric column (commas allowed); non-numeric text stops the run."""
    clean = pl.col(column).str.replace_all(",", "")
    parsed = clean.cast(pl.Float64, strict=False)
    bad = df.filter(parsed.is_null() & pl.col(column).is_not_null() & (pl.col(column) != ""))
    if len(bad):
        raise ValueError(f"{what}: non-numeric {column} values {bad[column].unique().to_list()[:5]}")
    return parsed


def whole(values: pl.Expr, column: str, df: pl.DataFrame, what: str) -> pl.Expr:
    frac = df.filter(values.is_not_null() & (values != values.round(0)))
    if len(frac):
        raise ValueError(f"{what}: fractional {column} values {frac[column].head(3).to_list()}")
    return values.cast(pl.Int64)


def state_ids() -> dict[str, str]:
    return {loc["usps"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}


def transform_states(body: bytes) -> pl.DataFrame:
    what = f"VSRR {STATE_ID}"
    df = read(body, STATE_COLUMNS, what)
    if set(df["Period"].unique()) != {"12 month-ending"}:
        raise ValueError(f"{what}: unexpected periods {df['Period'].unique().to_list()}")
    unknown = sorted(set(df["Indicator"].unique()) - set(INDICATORS) - {SPECIFIED})
    if unknown:
        raise ValueError(f"{what}: unknown indicators {unknown}")
    months = sorted(set(df["Month"].unique()) - set(MONTHS))
    if months:
        raise ValueError(f"{what}: unknown month names {months}")
    states = state_ids()
    places = sorted(set(df["State"].unique()) - set(states) - {"US", "YC"})
    if places:
        raise ValueError(f"{what}: unknown places {places}")
    footnote = pl.col("Footnote").fill_null("")
    df = df.with_columns(
        (pl.col("Year") + "-" + pl.col("Month").replace_strict(MONTHS).cast(pl.Utf8).str.zfill(2)).alias("date"),
        number(df, "Data Value", what).alias("value"),
        number(df, "Predicted Value", what).alias("predicted"),
        number(df, "Percent Complete", what).alias("percent_complete"),
        number(df, "Percent Pending Investigation", what).alias("percent_pending"),
        footnote.str.contains("suppressed", literal=True).alias("suppressed"),
        footnote.str.contains("Underreported", literal=True).alias("underreported"),
    )
    specified = df.filter(pl.col("Indicator") == SPECIFIED).select("State", "date", pl.col("value").alias("pct_drugs_specified"))
    counts = df.filter(pl.col("Indicator") != SPECIFIED)
    counts = counts.with_columns(
        whole(pl.col("value"), "Data Value", counts, what).alias("deaths"),
        whole(pl.col("predicted"), "Predicted Value", counts, what).alias("predicted_deaths"),
    ).join(specified, on=["State", "date"], how="left")
    columns = ["deaths", "predicted_deaths", "percent_complete", "percent_pending", "pct_drugs_specified", "suppressed", "underreported"]
    counts = counts.select(pl.col("Indicator").replace_strict(INDICATORS).alias("series"), "State", "date", *columns)

    # New York = New York excluding NYC ("NY") + New York City ("YC"). Counts add up only where both are shown.
    ny = (
        counts.filter(pl.col("State").is_in(["NY", "YC"]))
        .group_by("series", "date")
        .agg(
            pl.len().alias("parts"),
            pl.when(pl.col("deaths").null_count() == 0).then(pl.col("deaths").sum()).alias("deaths"),
            pl.when(pl.col("predicted_deaths").null_count() == 0).then(pl.col("predicted_deaths").sum()).alias("predicted_deaths"),
            pl.col("suppressed").any(),
            pl.col("underreported").any(),
        )
    )
    unpaired = ny.filter(pl.col("parts") != 2)
    if len(unpaired):
        raise ValueError(f"{what}: New York without New York City (or the reverse) for {unpaired.select('series', 'date').head(3).rows()}")
    ny = ny.select(
        "series", pl.lit("us-ny").alias("location"), "date", "deaths", "predicted_deaths",
        pl.lit(None, pl.Float64).alias("percent_complete"), pl.lit(None, pl.Float64).alias("percent_pending"),
        pl.lit(None, pl.Float64).alias("pct_drugs_specified"), "suppressed", "underreported",
    )  # fmt: skip
    rest = counts.filter(~pl.col("State").is_in(["NY", "YC"])).select(
        "series",
        pl.when(pl.col("State") == "US").then(pl.lit("usa")).otherwise(pl.col("State").replace(states)).alias("location"),
        "date",
        *columns,
    )
    out = pl.concat([rest, ny])
    dupes = out.select("series", "location", "date").is_duplicated().sum()
    if dupes:
        raise ValueError(f"{what}: {dupes} rows repeat a series, place and month")
    return out


def transform_counties(body: bytes) -> pl.DataFrame:
    what = f"VSRR {COUNTY_ID}"
    df = read(body, COUNTY_COLUMNS, what)
    bad = df.filter(~pl.col("FIPS").str.contains(r"^\d{4,5}$") | ~pl.col("Month").str.contains(r"^\d{1,2}$"))
    if len(bad):
        raise ValueError(f"{what}: unexpected FIPS or month values {bad.select('FIPS', 'Month').head(5).rows()}")
    footnote = pl.col("Footnote").fill_null("")
    deaths = number(df, "Provisional Drug Overdose Deaths", what)
    df = df.with_columns(
        ("us-county-" + pl.col("FIPS").str.zfill(5)).alias("location"),
        (pl.col("Year") + "-" + pl.col("Month").str.zfill(2)).alias("date"),
        whole(deaths, "Provisional Drug Overdose Deaths", df, what).alias("deaths"),
        footnote.str.contains("counts between 1-9", literal=True).alias("suppressed"),
        number(df, "Percentage Of Records Pending Investigation", what).alias("percent_pending"),
    ).select("location", "date", "deaths", "suppressed", "percent_pending")
    # Merged Virginia cities appear twice under their county's code with the same figures.
    distinct = df.unique()
    clash = distinct.group_by("location", "date").len().filter(pl.col("len") > 1)
    if len(clash):
        raise ValueError(f"{what}: counties listed twice with different figures: {clash.head(5).rows()}")
    return distinct.with_columns(pl.lit("all-drugs").alias("series")).select("series", "location", "date", "deaths", "suppressed", "percent_pending")
