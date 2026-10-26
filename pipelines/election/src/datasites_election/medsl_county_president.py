"""County presidential returns, 2000-2024, from MEDSL's "County Presidential Election Returns 2000-2024"
(doi:10.7910/DVN/VOQCHQ).

Dataverse asks for a guestbook response before it serves this file, so a person downloads it into
data/sources/medsl/ and collect() reads it from there (see that folder's README). Dataverse's metadata
needs no guestbook, so collect() checks that copy's MD5 against the cited version and stops when MEDSL
publishes a new one.

Source columns: state,county_name,year,state_po,county_fips,office,candidate,party,candidatevotes,
totalvotes,version,mode

The output has one row per county (or other reporting unit) per candidate line: the county's total.
What the transform changes:
- Vote modes. Most counties have one TOTAL row per candidate. In 2020 and 2024 some states report
  only by vote mode (ABSENTEE, ELECTION DAY, ...; in 2024 some of those modes are blank). There the
  county's total is the sum of its mode rows, which matches MEDSL's totalvotes in every county, and
  from_modes is true. Where a county has TOTAL rows, its mode rows are dropped.
- Rows that aren't candidates are dropped. TOTAL VOTES CAST (2024) repeats totalvotes. UNDERVOTES,
  OVERVOTES and SPOILED (Arizona, Iowa and Vermont, 2024) are included in those counties' totalvotes,
  so their candidates add up to less than totalvotes.
- county_fips is zero-padded to five digits. It is null for units that aren't counties: Alaska's
  state house districts, Kansas City (Missouri), Connecticut's statewide write-ins, Maine's overseas
  ballots and Rhode Island's federal precinct. Oglala Lakota County, South Dakota, is 46102 in 2024 as
  in 2016 and 2020 (MEDSL's 2024 rows use 46113, its code before 2015, when it was Shannon County).
- county_id is the shared registry's id for counties that still exist. It is null for Connecticut's
  eight former counties (the registry has the planning regions that replaced them), Bedford City,
  Virginia (51515, part of Bedford County since 2013) and Shannon County, South Dakota (46113).
- unit_id tells reporting units apart: the FIPS code, or <state>-<name> for a unit without one.
  County names alone don't: several Virginia cities share a name with a county (BEDFORD, FAIRFAX,
  FRANKLIN, RICHMOND, ROANOKE).
- Where a county has two OTHER rows (eight California and seven Connecticut counties in 2024), they
  are added together, like the other minor candidates MEDSL counts as OTHER.
- MEDSL's NA vote counts (Chase Oliver in some New Mexico counties, 2024) become null.
"""

from __future__ import annotations

import io

import polars as pl

from datasites_core import geo

from . import medsl

DOI = "doi:10.7910/DVN/VOQCHQ"
FILENAME = "countypres_2000-2024.csv"
SOURCE_COLUMNS = [
    "state", "county_name", "year", "state_po", "county_fips", "office", "candidate", "party",
    "candidatevotes", "totalvotes", "version", "mode",
]  # fmt: skip
PARTIES = {"DEMOCRAT", "REPUBLICAN", "LIBERTARIAN", "GREEN", "OTHER"}
NOT_CANDIDATES = {"TOTAL VOTES CAST", "UNDERVOTES", "OVERVOTES", "SPOILED"}
# (state_po, county_name, MEDSL's code) -> the county's FIPS code
FIPS_FIXES = {("SD", "OGLALA LAKOTA", "46113"): "46102"}
UNIT = ["year", "state_po", "unit_id"]
WHAT = "MEDSL county presidential"


def collect(ctx) -> dict[str, bytes]:
    body = medsl.local_file(FILENAME)
    meta = ctx.get_json(medsl.dataset_api_url(), params={"persistentId": DOI})
    medsl.check_local_copy(meta, DOI, FILENAME, ctx.contract.source.attribution, body)
    return {"countypres": body}


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(raw["countypres"]), infer_schema_length=0)
    medsl.require_columns(df, SOURCE_COLUMNS, WHAT)
    df = df.with_columns(pl.col(c).str.strip_chars() for c in df.columns)
    df = df.with_columns(pl.when(pl.col(c).is_in(["", "NA"])).then(None).otherwise(pl.col(c)).alias(c) for c in df.columns)
    medsl.require_values(df, "office", {"US PRESIDENT"}, WHAT)
    medsl.require_values(df, "party", PARTIES, WHAT)
    bad_years = df.filter(~pl.col("year").str.contains(r"^\d{4}$"))
    if len(bad_years):
        raise ValueError(f"{WHAT}: unexpected year values {bad_years['year'].unique().to_list()[:5]}")
    unnamed = df.filter(pl.col("party").is_null() & ~pl.col("candidate").is_in(NOT_CANDIDATES))
    if len(unnamed):
        raise ValueError(f"{WHAT}: rows with no party that aren't vote totals: {unnamed['candidate'].unique().to_list()[:5]}")

    states = {loc["usps"]: loc for loc in geo.load().values() if loc["level"] == "us-state"}
    unknown = sorted(set(df["state_po"].unique().to_list()) - set(states))
    if unknown:
        raise ValueError(f"{WHAT}: state codes not in the registry: {unknown}")
    counties = {loc["fips"] for loc in geo.load().values() if loc["level"] == "us-county"}

    df = df.with_columns(
        medsl.whole_number("candidatevotes", WHAT, df).alias("candidatevotes"),
        medsl.whole_number("totalvotes", WHAT, df).alias("totalvotes"),
        medsl.version_date(df, "version", WHAT).alias("version"),
        county_fips(df, {usps: loc["fips"] for usps, loc in states.items()}).alias("county_fips"),
    )
    df = df.with_columns(
        pl.coalesce("county_fips", pl.concat_str("state_po", pl.lit("-"), pl.col("county_name").str.replace_all(r"\s+", "-")))
        .alias("unit_id")
    )
    names = df.group_by(UNIT).agg(pl.col("county_name").n_unique().alias("n")).filter(pl.col("n") > 1)
    if len(names):
        raise ValueError(f"{WHAT}: one reporting unit has several names: {names.head(5).rows()}")
    totals = df.group_by(UNIT).agg(pl.col("totalvotes").n_unique().alias("n")).filter(pl.col("n") > 1)
    if len(totals):
        raise ValueError(f"{WHAT}: a county has several totalvotes values: {totals.head(5).rows()}")
    repeated = df.filter((pl.col("candidate") == "TOTAL VOTES CAST") & (pl.col("candidatevotes") != pl.col("totalvotes")))
    if len(repeated):
        raise ValueError(f"{WHAT}: TOTAL VOTES CAST differs from totalvotes in {len(repeated)} rows")

    df = df.filter(~pl.col("candidate").is_in(NOT_CANDIDATES))
    has_total = (pl.col("mode") == "TOTAL").any().over(UNIT)
    df = df.with_columns(has_total.alias("has_total")).filter(~pl.col("has_total") | (pl.col("mode") == "TOTAL"))
    line = [*UNIT, "candidate", "party"]
    out = (
        df.group_by(line, maintain_order=True)
        .agg(
            pl.col("state", "county_name", "county_fips", "office", "totalvotes", "version").first(),
            pl.when(pl.col("candidatevotes").is_null().any()).then(None).otherwise(pl.col("candidatevotes").sum()).alias("candidatevotes"),
            (~pl.col("has_total").first()).alias("from_modes"),
            pl.len().alias("rows"),
        )
    )
    # MEDSL lumps minor candidates into OTHER; a few 2024 counties (California, Connecticut) have two such rows.
    doubled = out.filter(~pl.col("from_modes") & (pl.col("rows") > 1) & (pl.col("candidate") != "OTHER"))
    if len(doubled):
        raise ValueError(f"{WHAT}: a candidate has several TOTAL rows in one county: {doubled.select(line).head(5).rows()}")
    summed = out.filter(pl.col("from_modes")).group_by(UNIT).agg(
        pl.col("candidatevotes").sum().alias("sum"), pl.col("totalvotes").first().alias("total"), pl.col("candidatevotes").null_count().alias("nulls")
    )
    mismatched = summed.filter((pl.col("nulls") == 0) & (pl.col("sum") != pl.col("total")))
    if len(mismatched):
        raise ValueError(f"{WHAT}: vote-mode rows don't add up to totalvotes in {mismatched.select(UNIT).head(5).rows()}")

    return out.select(
        "year",
        "state",
        "state_po",
        pl.lit("us-").add(pl.col("state_po").str.to_lowercase()).alias("state_id"),
        "county_name",
        "county_fips",
        pl.when(pl.col("county_fips").is_in(list(counties))).then(pl.lit("us-county-") + pl.col("county_fips")).alias("county_id"),
        "unit_id",
        "office",
        "candidate",
        "party",
        "candidatevotes",
        "totalvotes",
        "from_modes",
        "version",
    )


def county_fips(df: pl.DataFrame, state_fips: dict[str, str]) -> pl.Expr:
    """MEDSL's county_fips as a five-digit code, or null where the unit isn't a county.

    MEDSL drops leading zeros (1001 for 01001) and puts other codes in the same column: Alaska's state
    house districts (2001-2040), Kansas City's place code (2938000, or 36000 in 2024) and NA."""
    code = pl.col("county_fips")
    for (state_po, name, wrong), right in FIPS_FIXES.items():
        code = pl.when((pl.col("state_po") == state_po) & (pl.col("county_name") == name) & (code == wrong)).then(pl.lit(right)).otherwise(code)
    padded = code.str.zfill(5)
    prefix = pl.col("state_po").replace_strict(state_fips, default=None)
    is_county = code.str.contains(r"^\d{4,5}$") & (padded.str.slice(0, 2) == prefix) & (pl.col("state_po") != "AK")
    return pl.when(is_county).then(padded)

