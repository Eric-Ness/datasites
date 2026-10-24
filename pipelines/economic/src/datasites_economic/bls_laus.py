"""BLS Local Area Unemployment Statistics (LAUS): monthly labor force and unemployment for states,
metropolitan and micropolitan areas (CBSAs) and counties.

Source: the keyless flat files at https://download.bls.gov/pub/time.series/la/ (layout in la.txt).
Every data file is tab-separated with the columns series_id, year, period, value, footnote_codes.
A series id such as LAUCN010010000000003 breaks down as

    LA  U  CN0100100000000  03
    |   |  |                measure: 03 unemployment rate, 04 unemployment, 05 employment, 06 labor force
    |   |  area code (15 characters; see la.area)
    |   seasonal: S seasonally adjusted, U not seasonally adjusted
    survey

Area codes used here:
    ST<state FIPS>00000000000           statewide          -> us-<usps>
    CN<state FIPS><county FIPS>00000000 county             -> us-county-<5-digit FIPS>
    MT<state FIPS><CBSA code>000000     metropolitan area  -> us-cbsa-<5-digit CBSA code>
    MC<state FIPS><CBSA code>000000     micropolitan area  -> us-cbsa-<5-digit CBSA code>
The state FIPS in a CBSA area code is the CBSA's principal state; the CBSA code alone identifies it.

Quirks the transform handles:
  - the files mix CRLF and LF line endings, and pad the series id and value fields with spaces;
  - "-" marks a missing value (footnote X: the October 2025 shutdown; N: not available);
  - footnote P marks preliminary estimates (the latest month);
  - period M13 is the annual average, which this monthly dataset leaves out;
  - a footnote can also sit on the series itself in la.series (St. Louis MSA: A, boundaries differ
    from OMB's), which applies to every month of that series.
"""

from __future__ import annotations

import io

import polars as pl

from ._common import state_ids_by_fips

BASE = "https://download.bls.gov/pub/time.series/la/"
RESOURCES = {
    "la.area": BASE + "la.area",
    "la.series": BASE + "la.series",
    "states-nsa": BASE + "la.data.2.AllStatesU",
    "states-sa": BASE + "la.data.3.AllStatesS",
    "metro": BASE + "la.data.60.Metro",
    "micro": BASE + "la.data.62.Micro",
    "county": BASE + "la.data.64.County",
}
DATA_FILES = ("states-nsa", "states-sa", "metro", "micro", "county")

MEASURES = {"06": "labor_force", "05": "employed", "04": "unemployed", "03": "unemployment_rate"}
COUNTS = ("labor_force", "employed", "unemployed")
AREA_PREFIXES = ("ST", "CN", "MT", "MC")
SERIES = {"U": "nsa", "S": "sa"}
DATA_COLUMNS = ["series_id", "year", "period", "value", "footnote_codes"]


def _read_tsv(body: bytes, columns: list[str]) -> pl.DataFrame:
    """Every field as a string, padding and stray carriage returns removed."""
    df = pl.read_csv(
        io.BytesIO(body),
        separator="\t",
        has_header=True,
        new_columns=columns,
        infer_schema=False,
        quote_char=None,
        truncate_ragged_lines=True,
    )
    return df.select(pl.col(c).str.strip_chars(" \r") for c in columns)


def _parse_series_id(df: pl.DataFrame) -> pl.DataFrame:
    sid = pl.col("series_id")
    return df.with_columns(
        sid.str.slice(2, 1).alias("seasonal"),
        sid.str.slice(3, 15).alias("area_code"),
        sid.str.slice(18, 2).alias("measure"),
    )


def read_data(body: bytes) -> pl.DataFrame:
    """One la.data.* file -> monthly observations of the four labor-force measures in our area types."""
    df = _parse_series_id(_read_tsv(body, DATA_COLUMNS))
    df = df.filter(
        pl.col("measure").is_in(list(MEASURES))
        & pl.col("area_code").str.slice(0, 2).is_in(list(AREA_PREFIXES))
        & pl.col("period").str.contains(r"^M(0[1-9]|1[0-2])$")  # M13 is the annual average
    )
    value = pl.col("value")
    return df.select(
        "seasonal",
        "area_code",
        "measure",
        pl.format("{}-{}", pl.col("year"), pl.col("period").str.slice(1, 2)).alias("date"),
        pl.when(value.str.contains(r"^-?\d+(\.\d+)?$")).then(value.cast(pl.Float64)).otherwise(None).alias("value"),
        pl.col("footnote_codes").replace("", None).alias("footnotes"),
    )


def read_area_names(body: bytes) -> pl.DataFrame:
    """la.area: area_type_code, area_code, area_text, display_level, selectable, sort_sequence."""
    df = _read_tsv(body, ["area_type_code", "area_code", "area_text", "display_level", "selectable", "sort_sequence"])
    return df.select("area_code", pl.col("area_text").alias("location_name"))


def read_series_footnotes(body: bytes) -> pl.DataFrame:
    """Series-level footnotes from la.series, keyed like the data (seasonal, area code)."""
    columns = [
        "series_id", "area_type_code", "area_code", "measure_code", "seasonal", "srd_code",
        "series_title", "footnote_codes", "begin_year", "begin_period", "end_year", "end_period",
    ]  # fmt: skip
    df = _parse_series_id(_read_tsv(body, columns).drop("area_code", "seasonal"))
    df = df.filter(pl.col("measure").is_in(list(MEASURES)) & (pl.col("footnote_codes") != ""))
    return df.group_by("seasonal", "area_code").agg(pl.col("footnote_codes").str.join("").alias("series_footnotes"))


def location_ids(area_code: pl.Expr) -> pl.Expr:
    prefix = area_code.str.slice(0, 2)
    state_fips = area_code.str.slice(2, 2)
    return (
        pl.when(prefix == "ST")
        .then(state_fips.replace_strict(state_ids_by_fips(), default=None))
        .when(prefix == "CN")
        .then(pl.lit("us-county-") + area_code.str.slice(2, 5))
        .when(prefix.is_in(["MT", "MC"]))
        .then(pl.lit("us-cbsa-") + area_code.str.slice(4, 5))
    )


def _codes(expr: pl.Expr) -> pl.Expr:
    """'XP' / 'P,X' / null -> 'P,X' (sorted, distinct single-letter codes) or null."""
    joined = expr.str.extract_all(r"[A-Z]").list.unique().list.sort().list.join(",")
    return pl.when(joined == "").then(None).otherwise(joined)


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    long = pl.concat([read_data(raw[name]) for name in DATA_FILES])
    # Each file covers distinct series; a duplicate would mean BLS changed the file layout.
    keys = ["seasonal", "area_code", "measure", "date"]
    if long.select(keys).is_duplicated().any():
        raise ValueError("LAUS data files contain the same series and month more than once")

    # One row per area and month with the four measures side by side. A measure BLS doesn't publish
    # for that area and month stays null; footnotes are the union over the four measures.
    wide = long.pivot(on="measure", index=["seasonal", "area_code", "date"], values=["value", "footnotes"])
    wide = wide.with_columns(
        pl.lit(None, dtype=pl.String if kind == "footnotes" else pl.Float64).alias(f"{kind}_{code}")
        for code in MEASURES
        for kind in ("value", "footnotes")
        if f"{kind}_{code}" not in wide.columns
    )
    wide = (
        wide.join(read_series_footnotes(raw["la.series"]), on=["seasonal", "area_code"], how="left")
        .join(read_area_names(raw["la.area"]), on="area_code", how="left")
        .with_columns(
            *[pl.col(f"value_{code}").alias(name) for code, name in MEASURES.items()],
            _codes(
                pl.concat_str([*[f"footnotes_{code}" for code in MEASURES], "series_footnotes"], ignore_nulls=True)
            ).alias("footnotes"),
        )
    )
    if wide["location_name"].null_count():
        missing = wide.filter(pl.col("location_name").is_null())["area_code"].unique().sort().to_list()
        raise ValueError(f"LAUS area codes missing from la.area: {missing[:10]}")
    out = wide.select(
        pl.col("seasonal").replace_strict(SERIES).alias("series"),
        location_ids(pl.col("area_code")).alias("location"),
        "date",
        *[pl.col(name).round(0).cast(pl.Int64) for name in COUNTS],
        pl.col("unemployment_rate"),
        pl.col("footnotes").str.contains("P").fill_null(False).alias("preliminary"),
        "footnotes",
        "location_name",
    )
    if out["location"].null_count():
        bad = wide.filter(location_ids(pl.col("area_code")).is_null())["area_code"].unique().to_list()
        raise ValueError(f"LAUS statewide area codes with no state in the registry: {bad[:10]}")
    return out
