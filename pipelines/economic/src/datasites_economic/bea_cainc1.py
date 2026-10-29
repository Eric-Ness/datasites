"""BEA Regional table CAINC1: personal income, population and per capita personal income for every
county, state and the nation, annually from 1969.

Source: BEA's keyless bulk download of the table, https://apps.bea.gov/regional/zip/CAINC1.zip, one
conditional GET per check. The zip holds a CSV per state and CAINC1__ALL_AREAS_<first>_<last>.csv with
every area; only that file is read. It has one row per area and line code (1 personal income,
thousands of dollars; 2 population, persons; 3 per capita personal income, dollars), one column per
year, and a few lines of notes after the data. It is Latin-1 ("Doña Ana, NM").

The BEA Data API serves the same table but needs a registered UserID; the bulk file needs none and
is one 3.5 MB request instead of six 30 MB ones.

Geography (GeoFIPS):
  00000                 -> usa
  <state FIPS>000       -> us-<usps>; BEA regions 91000-98000 are left out
  <state><county 000-899> -> us-county-<FIPS>: current counties plus legacy codes BEA still carries
                           (Connecticut's eight counties, older Alaska census areas)
  <state><county 900-999> -> bea-<GeoFIPS>: BEA-only combination areas (Virginia independent cities
                           merged with a county, Maui + Kalawao, Shawano + Menominee before 1989,
                           pre-1979 Alaska census divisions), named from GeoName
"""

from __future__ import annotations

import io
import re
import zipfile

import polars as pl

from ._common import state_ids_by_fips

TABLE = "CAINC1"
RESOURCES = {"cainc1": "https://apps.bea.gov/regional/zip/CAINC1.zip"}
LINES = {
    "1": ("personal_income", "Thousands of dollars"),
    "2": ("population", "Number of persons"),
    "3": ("per_capita_personal_income", "Dollars"),
}
ALL_AREAS = re.compile(rf"{TABLE}__ALL_AREAS_\d{{4}}_\d{{4}}\.csv")


def all_areas_csv(body: bytes) -> bytes:
    """The every-area CSV inside BEA's zip (or the body itself when it is already that CSV)."""
    if body[:2] != b"PK":
        return body
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        names = [n for n in zf.namelist() if ALL_AREAS.fullmatch(n)]
        if len(names) != 1:
            raise ValueError(f"expected one {TABLE}__ALL_AREAS_*.csv in BEA's zip, found {zf.namelist()}")
        return zf.read(names[0])


def normalize(_name: str, body: bytes) -> bytes:
    """Change detection on the CSV, so a re-zipped but identical table doesn't trigger a rebuild."""
    return all_areas_csv(body)


def _number(text: pl.Expr) -> pl.Expr:
    """'45359' -> 45359.0; '(NA)', '(D)', '(NM)', '(L)' and other markers -> null."""
    clean = text.str.replace_all(",", "").str.strip_chars()
    return pl.when(clean.str.contains(r"^-?\d+(\.\d+)?$")).then(clean.cast(pl.Float64)).otherwise(None)


def read_table(body: bytes) -> pl.DataFrame:
    """BEA's CAINC1 CSV (or its zip) -> one row per area, line and year: fips, geo_name, line, date, value."""
    text = all_areas_csv(body).decode("latin-1")
    df = pl.read_csv(io.StringIO(text), infer_schema=False, truncate_ragged_lines=True)
    required = {"GeoFIPS", "GeoName", "TableName", "LineCode", "Unit"}
    if not required <= set(df.columns):
        raise ValueError(f"BEA {TABLE} CSV: columns {sorted(required - set(df.columns))} not found")
    years = [c for c in df.columns if re.fullmatch(r"\d{4}", c)]
    if not years:
        raise ValueError(f"BEA {TABLE} CSV: no year columns")
    # The notes after the data ("Note: See the included footnote file.", "Last updated: ...") have no line code.
    df = df.filter(pl.col("LineCode").is_not_null()).with_columns(
        pl.col("GeoFIPS").str.strip_chars(' "'), pl.col("LineCode").str.strip_chars(), pl.col("TableName").str.strip_chars()
    )
    if df.is_empty():
        raise ValueError(f"BEA {TABLE} CSV: no data rows")
    for line, (_, unit) in LINES.items():
        bad = df.filter((pl.col("LineCode") == line) & ((pl.col("TableName") != TABLE) | (pl.col("Unit").str.strip_chars() != unit)))
        if len(bad):
            row = bad.row(0, named=True)
            raise ValueError(f"BEA {TABLE} line {line}: expected {unit}, got {row['TableName']} {row['Unit']}")
    return (
        df.filter(pl.col("LineCode").is_in(list(LINES)))
        .unpivot(index=["GeoFIPS", "GeoName", "LineCode"], on=years, variable_name="date", value_name="value")
        .select(
            pl.col("GeoFIPS").alias("fips"),
            pl.col("GeoName").alias("geo_name"),
            pl.col("LineCode").alias("line"),
            "date",
            _number(pl.col("value")).alias("value"),
        )
    )


def location_ids(fips: pl.Expr) -> pl.Expr:
    state, county = fips.str.slice(0, 2), fips.str.slice(2, 3)
    return (
        pl.when(fips == "00000")
        .then(pl.lit("usa"))
        .when((county == "000") & (state.cast(pl.Int64) >= 91))
        .then(None)  # BEA regions
        .when(county == "000")
        .then(state.replace_strict(state_ids_by_fips(), default=None))
        .when(county.cast(pl.Int64) >= 900)
        .then(pl.lit("bea-") + fips)
        .otherwise(pl.lit("us-county-") + fips)
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    """A line missing from BEA's file leaves its column null, which the contract's max_null_fraction
    checks then block, so a partial table can never be published."""
    long = read_table(raw["cainc1"])
    measures = [name for name, _ in LINES.values()]
    wide = long.pivot(on="line", index=["fips", "geo_name", "date"], values="value", aggregate_function="first")
    wide = wide.rename({line: name for line, (name, _) in LINES.items() if line in wide.columns})
    wide = wide.with_columns([pl.lit(None, dtype=pl.Float64).alias(name) for name in measures if name not in wide.columns])
    wide = wide.with_columns(location_ids(pl.col("fips")).alias("location")).filter(
        pl.col("location").is_not_null() & pl.any_horizontal(pl.col(measures).is_not_null())
    )
    return wide.sort("fips", "date").select(
        pl.lit("annual").alias("series"),
        "location",
        "date",
        *[pl.col(m).round(0).cast(pl.Int64) for m in measures],
        pl.col("geo_name").str.strip_chars(" *").alias("location_name"),
    )
