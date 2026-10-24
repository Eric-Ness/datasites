"""Yearly AQI summary by US county from EPA AirData's annual_aqi_by_county_YYYY.zip (1980-).

Columns: "State","County","Year","Days with AQI","Good Days","Moderate Days",
  "Unhealthy for Sensitive Groups Days","Unhealthy Days","Very Unhealthy Days","Hazardous Days","Max AQI",
  "90th Percentile AQI","Median AQI","Days CO","Days NO2","Days Ozone","Days PM2.5","Days PM10"

The annual files name counties ("California", "Los Angeles") but carry no codes. The codes come from
AirData's own site list, aqs_sites.zip, which gives "State Name" and "County Name" next to "State Code" and
"County Code" for every AQS site, in the same spelling the summary files use ("Baltimore (City)", "Saint
Louis", "Dona Ana"). Matching on the registry's names would need guesses for those; this needs none. A name
missing from the site list, or naming two codes, stops the run.
"""

from __future__ import annotations

import re

import polars as pl

from .aqi import (
    AIRDATA,
    AIRDATA_PAGE,
    airdata_years,
    county_location,
    normalize_zip,
    read_airdata_csv,
    us_counties,
)

PREFIX = "annual_aqi_by_county"
SERIES = "summary"
SITES_URL = f"{AIRDATA}/aqs_sites.zip"
NON_US_STATES = ("Country Of Mexico", "Canada")
COLUMNS = {
    "State": "state_name",
    "County": "county_name",
    "Year": "date",
    "Days with AQI": "days_with_aqi",
    "Good Days": "good_days",
    "Moderate Days": "moderate_days",
    "Unhealthy for Sensitive Groups Days": "unhealthy_sensitive_days",
    "Unhealthy Days": "unhealthy_days",
    "Very Unhealthy Days": "very_unhealthy_days",
    "Hazardous Days": "hazardous_days",
    "Max AQI": "max_aqi",
    "90th Percentile AQI": "aqi_90th_percentile",
    "Median AQI": "median_aqi",
    "Days CO": "days_co",
    "Days NO2": "days_no2",
    "Days Ozone": "days_ozone",
    "Days PM2.5": "days_pm25",
    "Days PM10": "days_pm10",
}
CATEGORY_DAYS = [
    "good_days",
    "moderate_days",
    "unhealthy_sensitive_days",
    "unhealthy_days",
    "very_unhealthy_days",
    "hazardous_days",
]
POLLUTANT_DAYS = ["days_co", "days_no2", "days_ozone", "days_pm25", "days_pm10"]
NUMBERS = [
    c for c in COLUMNS.values() if c not in ("state_name", "county_name", "date")
]
YEAR_NAME = re.compile(r"^airdata-(\d{4})$")


def resources(ctx) -> dict[str, str]:
    page = ctx.get(AIRDATA_PAGE).decode("utf-8", "replace")
    res = {
        f"airdata-{y}": f"{AIRDATA}/{PREFIX}_{y}.zip"
        for y in airdata_years(page, PREFIX)
    }
    res["aqs-sites"] = SITES_URL
    return res


normalize = normalize_zip


def _names() -> list[pl.Expr]:
    """Older files pad some names with a trailing space ("Anchorage "); compare names without it."""
    return [
        pl.col("state_name").str.strip_chars(),
        pl.col("county_name").str.strip_chars(),
    ]


def crosswalk(sites_body: bytes) -> pl.DataFrame:
    """(state_name, county_name) -> 5-digit FIPS, from AQS's site list."""
    cols = {
        "State Code": "state",
        "County Code": "county",
        "State Name": "state_name",
        "County Name": "county_name",
    }
    sites = read_airdata_csv(sites_body, cols, "aqs_sites")
    pairs = sites.select(_names(), fips=pl.col("state") + pl.col("county")).unique()
    ambiguous = (
        pairs.group_by("state_name", "county_name")
        .agg(pl.col("fips").sort())
        .filter(pl.col("fips").list.len() > 1)
    )
    if len(ambiguous):
        raise ValueError(
            f"aqs_sites: county names with more than one code: {ambiguous.head(5).rows()}"
        )
    return pairs


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name in sorted(raw):
        m = YEAR_NAME.match(name)
        if not m:
            continue
        df = read_airdata_csv(raw[name], COLUMNS, f"{PREFIX}_{m.group(1)}")
        if len(df) and set(df["date"].unique()) != {m.group(1)}:
            raise ValueError(
                f"{PREFIX}_{m.group(1)}: rows for other years {sorted(set(df['date']) - {m.group(1)})}"
            )
        frames.append(df)
    if not frames:
        raise ValueError("no AirData annual files in the raw inputs")
    table = pl.concat(frames).with_columns(
        *_names(), *[pl.col(c).cast(pl.Int64, strict=True) for c in NUMBERS]
    )
    table = table.filter(~pl.col("state_name").is_in(NON_US_STATES))

    table = table.join(
        crosswalk(raw["aqs-sites"]), on=["state_name", "county_name"], how="left"
    )
    unmatched = (
        table.filter(pl.col("fips").is_null())
        .select("state_name", "county_name")
        .unique()
        .sort("state_name", "county_name")
    )
    if len(unmatched):
        raise ValueError(
            f"{len(unmatched)} AirData county names not in aqs_sites.zip: {unmatched.head(10).rows()}"
        )
    table = us_counties(table)

    for parts, label in (
        (CATEGORY_DAYS, "AQI category"),
        (POLLUTANT_DAYS, "defining-pollutant"),
    ):
        bad = table.filter(pl.sum_horizontal(parts) != pl.col("days_with_aqi"))
        if len(bad):
            row = bad.row(0, named=True)
            raise ValueError(
                f"{len(bad)} county-years whose {label} days don't add up to 'Days with AQI' (e.g. {row['state_name']}, {row['county_name']} {row['date']})"
            )

    return table.select(
        pl.lit(SERIES).alias("series"),
        county_location(pl.col("fips")).alias("location"),
        "date",
        *NUMBERS,
    )
