"""BEA Regional table CAINC1: personal income, population and per capita personal income for every
county, state and the nation, annually from 1969.

Source: the BEA Data API (https://apps.bea.gov/api/data), dataset Regional, GetData method. It needs
a free UserID, read from BEA_API_KEY. Six requests per run: line codes 1 (personal income,
thousands of dollars), 2 (population, persons) and 3 (per capita personal income, dollars), each
for GeoFips=STATE (the nation, states and BEA regions) and GeoFips=COUNTY, with Year=ALL.

BEA limits each user to 100 requests, 100 MB and 30 errors a minute; a county request with every
year is about 30 MB, so county requests are spaced PAUSE_SECONDS apart.

Every response echoes the request, UserID included, in BEAAPI.Request. collect() keeps only
BEAAPI.Results (minus the per-request UTCProductionTime), so the key never reaches the raw archive
and an unchanged table hashes the same from run to run.

Geography (GeoFips):
  00000                 -> usa
  <state FIPS>000       -> us-<usps>; BEA regions 91000-98000 are left out
  <state><county 000-899> -> us-county-<FIPS>: current counties plus legacy codes BEA still carries
                           (Connecticut's eight counties, older Alaska census areas)
  <state><county 900-999> -> bea-<GeoFips>: BEA-only combination areas (Virginia independent cities
                           merged with a county, Maui + Kalawao, Shawano + Menominee before 1989,
                           pre-1979 Alaska census divisions), named from GeoName
"""

from __future__ import annotations

import json
import time
from typing import Any

import polars as pl

from ._common import keyed_get, state_ids_by_fips

API = "https://apps.bea.gov/api/data"
TABLE = "CAINC1"
LINES = {
    "1": ("personal_income", "Thousands of dollars", "3"),
    "2": ("population", "Number of persons", "0"),
    "3": ("per_capita_personal_income", "Dollars", "0"),
}
GEOS = ("STATE", "COUNTY")
PAUSE_SECONDS = 45.0


def _results(body: bytes, what: str) -> dict:
    doc = json.loads(body)
    results = doc.get("BEAAPI", {}).get("Results") if isinstance(doc, dict) else None
    if isinstance(results, list):  # some BEA methods wrap Results in a one-element list
        results = results[0] if results else None
    if not isinstance(results, dict):
        raise RuntimeError(f"{what}: unexpected BEA API response")
    if "Error" in results:
        error = results["Error"]
        raise RuntimeError(f"{what}: BEA API error {error.get('APIErrorCode')}: {error.get('APIErrorDescription')}")
    return results


def collect(ctx: Any) -> dict[str, bytes]:
    key = ctx.env("BEA_API_KEY")
    out: dict[str, bytes] = {}
    first = True
    for line in LINES:
        for geo in GEOS:
            if geo == "COUNTY" and not first:
                time.sleep(PAUSE_SECONDS)
            first = False
            what = f"BEA {TABLE} line {line} {geo}"
            params = {
                "UserID": key,
                "method": "GetData",
                "datasetname": "Regional",
                "TableName": TABLE,
                "LineCode": line,
                "GeoFips": geo,
                "Year": "ALL",
                "ResultFormat": "json",
            }
            results = _results(keyed_get(ctx, API, params, what), what)
            results.pop("UTCProductionTime", None)
            out[f"line{line}-{geo.lower()}"] = json.dumps(results, sort_keys=True, ensure_ascii=False).encode()
    return out


def _number(text: pl.Expr) -> pl.Expr:
    """'45,359' -> 45359.0; '(NA)', '(D)', '(NM)', '(L)' and other markers -> null."""
    clean = text.str.replace_all(",", "").str.strip_chars()
    return pl.when(clean.str.contains(r"^-?\d+(\.\d+)?$")).then(clean.cast(pl.Float64)).otherwise(None)


def read_results(body: bytes, line: str) -> pl.DataFrame:
    """One GetData result (BEAAPI.Results, or a whole BEAAPI response) -> GeoFips, year and value."""
    doc = json.loads(body)
    results = _results(body, f"BEA {TABLE} line {line}") if "BEAAPI" in doc else doc
    name, unit, mult = LINES[line]
    rows = results.get("Data") or []
    df = pl.DataFrame(
        [(r.get("Code"), r["GeoFips"], r.get("GeoName"), r["TimePeriod"], r.get("CL_UNIT"), r.get("UNIT_MULT"), r.get("DataValue")) for r in rows],
        schema=["code", "fips", "geo_name", "date", "unit", "mult", "value"],
        orient="row",
    )
    if df.is_empty():
        raise ValueError(f"BEA {TABLE} line {line}: no data rows")
    unit_matches = pl.col("unit").str.strip_chars().str.to_lowercase() == unit.lower()
    bad = df.filter((pl.col("code") != f"{TABLE}-{line}") | ~unit_matches | (pl.col("mult").str.strip_chars() != mult))
    if len(bad):
        row = bad.row(0, named=True)
        raise ValueError(f"BEA {TABLE} line {line}: expected {unit} (UNIT_MULT {mult}), got {row['code']} {row['unit']} {row['mult']}")
    return df.select("fips", "geo_name", "date", _number(pl.col("value")).alias(name))


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
    """collect() always returns all six results. A line missing here leaves its column null, which the
    contract's max_null_fraction checks then block, so a partial table can never be published."""
    measures = [name for name, _, _ in LINES.values()]
    wide = pl.DataFrame(schema={"fips": pl.String, "date": pl.String, "geo_name": pl.String})
    for line, (name, _, _) in LINES.items():
        parts = [read_results(raw[key], line) for geo in GEOS if (key := f"line{line}-{geo.lower()}") in raw]
        if not parts:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
            continue
        # The STATE and COUNTY results don't overlap; if BEA ever repeats a row, keep one copy.
        part = pl.concat(parts).unique(subset=["fips", "date"], keep="first", maintain_order=True)
        wide = (
            wide.join(part, on=["fips", "date"], how="full", coalesce=True)
            .with_columns(pl.coalesce("geo_name", "geo_name_right").alias("geo_name"))
            .drop("geo_name_right")
        )
    wide = wide.with_columns(location_ids(pl.col("fips")).alias("location")).filter(
        pl.col("location").is_not_null() & pl.any_horizontal(pl.col(measures).is_not_null())
    )
    return wide.select(
        pl.lit("annual").alias("series"),
        "location",
        "date",
        *[pl.col(m).round(0).cast(pl.Int64) for m in measures],
        pl.col("geo_name").str.strip_chars(" *").alias("location_name"),
    )
