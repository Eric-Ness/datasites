"""Health insurance coverage by county and state from the Census Bureau's Small Area Health Insurance Estimates.

One zip per year (2008-), www2.census.gov/programs-surveys/sahie/datasets/time-series/estimates-acs/
sahie-{YYYY}-csv.zip, holding sahie_{YYYY}.csv: about 80 lines describing the layout, then a CSV with columns
  year, version, statefips, countyfips, geocat, agecat, racecat, sexcat, iprcat, NIPR, nipr_moe, NUI, nui_moe,
  NIC, nic_moe, PCTUI, pctui_moe, PCTIC, pctic_moe, PCTELIG, pctelig_moe, PCTLIIC, pctliic_moe, state_name,
  county_name
with one row per place, age group, race, sex and income group. Kept: all races, both sexes, all income levels,
for three age groups (under 65, 18-64, under 19). States (geocat 40) and counties (geocat 50) only; race is
broken down for states alone, so it is left out.

2013 was published twice; the "Updated" version (May 2016) replaces the "Original". Census says Kalawao
County, HI (15005) values "should be considered N/A or missing": they become null. A margin of error of 0 means
under 1 person or under 0.1 point, and is kept as 0.
"""

from __future__ import annotations

import io
import re
import zipfile

import polars as pl

BASE = "https://www2.census.gov/programs-surveys/sahie/datasets/time-series/estimates-acs"
FIRST_YEAR = 2008
FILE = re.compile(r"\bsahie-(\d{4})-csv\.zip\b")
NAME = re.compile(r"^sahie-(\d{4})$")
HEADER = (
    "year,version,statefips,countyfips,geocat,agecat,racecat,sexcat,iprcat,NIPR,nipr_moe,NUI,nui_moe,NIC,nic_moe,"
    "PCTUI,pctui_moe,PCTIC,pctic_moe,PCTELIG,pctelig_moe,PCTLIIC,pctliic_moe,state_name,county_name"
)
AGES = {"0": "under-65", "1": "adults-18-64", "4": "children-under-19"}
VALUES = {
    "PCTUI": "uninsured_pct",
    "pctui_moe": "uninsured_pct_moe",
    "NUI": "uninsured",
    "nui_moe": "uninsured_moe",
    "NIPR": "population",
    "nipr_moe": "population_moe",
}
KALAWAO = "us-county-15005"


def resources(ctx) -> dict[str, str]:
    """Every year's zip from 2008 on, as listed in the directory (each has a Last-Modified, so reruns cost 304s)."""
    listing = ctx.get(f"{BASE}/").decode("utf-8", "replace")
    years = sorted({int(y) for y in FILE.findall(listing) if int(y) >= FIRST_YEAR})
    if not years or years[0] != FIRST_YEAR:
        raise ValueError(f"SAHIE directory: expected yearly files from {FIRST_YEAR}, found {years[:3]}")
    missing = sorted(set(range(FIRST_YEAR, years[-1] + 1)) - set(years))
    if missing:
        raise ValueError(f"SAHIE directory: yearly files missing for {missing}")
    return {f"sahie-{y}": f"{BASE}/sahie-{y}-csv.zip" for y in years}


def read_year(body: bytes, year: int) -> pl.DataFrame:
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if names != [f"sahie_{year}.csv"]:
            raise ValueError(f"SAHIE {year}: expected sahie_{year}.csv in the zip, found {zf.namelist()}")
        text = zf.read(names[0]).decode("latin-1")
    start = text.find(f"\n{HEADER}")
    if start < 0:
        raise ValueError(f"SAHIE {year}: the column header changed (expected {HEADER})")
    df = pl.read_csv(io.StringIO(text[start + 1 :]), infer_schema_length=0, truncate_ragged_lines=True)
    df = df.select(HEADER.split(",")).with_columns(pl.col(c).str.strip_chars() for c in HEADER.split(","))
    years = set(df["year"].unique())
    if years != {str(year)}:
        raise ValueError(f"SAHIE {year}: rows for years {sorted(years)} in sahie_{year}.csv")
    return df


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = []
    for name in sorted(raw):
        m = NAME.match(name)
        if not m:
            continue
        df = read_year(raw[name], int(m.group(1)))
        versions = set(df["version"].fill_null("").unique())
        if versions - {"", "Original", "Updated"}:
            raise ValueError(f"SAHIE {m.group(1)}: unexpected versions {sorted(versions)}")
        if "Updated" in versions:
            df = df.filter(pl.col("version") == "Updated")
        df = df.filter(
            pl.col("geocat").is_in(["40", "50"])
            & pl.col("agecat").is_in(list(AGES))
            & (pl.col("racecat") == "0")
            & (pl.col("sexcat") == "0")
            & (pl.col("iprcat") == "0")
        )
        frames.append(df)
    if not frames:
        raise ValueError("SAHIE: no yearly files")
    df = pl.concat(frames)
    bad = df.filter(~pl.col("statefips").str.contains(r"^\d{2}$") | ~pl.col("countyfips").str.contains(r"^\d{3}$"))
    if len(bad):
        raise ValueError(f"SAHIE: unexpected FIPS codes {bad.select('statefips', 'countyfips').head(5).rows()}")
    location = (
        pl.when(pl.col("geocat") == "40")
        .then(pl.lit("us-state-fips-") + pl.col("statefips"))
        .otherwise(pl.lit("us-county-") + pl.col("statefips") + pl.col("countyfips"))
    )
    values = []
    for source, column in VALUES.items():
        parsed = pl.col(source).cast(pl.Float64, strict=False)
        junk = df.filter(parsed.is_null() & pl.col(source).is_not_null() & ~pl.col(source).is_in(["", ".", "N/A"]))
        if len(junk):
            raise ValueError(f"SAHIE: non-numeric {source} values {junk[source].unique().to_list()[:5]}")
        values.append((parsed.cast(pl.Int64) if column in ("uninsured", "uninsured_moe", "population", "population_moe") else parsed).alias(column))
    out = df.select(
        pl.col("agecat").replace_strict(AGES).alias("series"),
        location.alias("location"),
        pl.col("year").alias("date"),
        *values,
    )
    from datasites_core import geo

    states = {f"us-state-fips-{loc['fips']}": loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state"}
    unknown = sorted(set(out.filter(pl.col("location").str.starts_with("us-state-fips-"))["location"]) - set(states))
    if unknown:
        raise ValueError(f"SAHIE: state codes not in the registry: {unknown}")
    out = out.with_columns(pl.col("location").replace(states))
    # Census: Kalawao's values "should be considered N/A or missing".
    out = out.with_columns(
        pl.when(pl.col("location") == KALAWAO).then(None).otherwise(pl.col(c)).alias(c) for c in VALUES.values()
    )
    dupes = out.select("series", "location", "date").is_duplicated().sum()
    if dupes:
        raise ValueError(f"SAHIE: {dupes} rows repeat a series, place and year")
    return out
