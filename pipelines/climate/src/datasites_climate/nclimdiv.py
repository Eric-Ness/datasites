"""Shared parsing for NOAA NCEI nClimDiv fixed-width files (county and statewide/national).

The files live in one folder whose old address (/pub/data/cirs/climdiv/) now answers 301 to BASE.
Every file name ends in the processing date from procdate.txt, so the URLs change each month:
    climdiv-tmpccy-v1.0.0-20261006       county average temperature
    climdiv-norm-tmpccy-v1.0.0-20261006  county "normals" for ten 30-year periods, 1901-2000 and 1895-2020

County record (county-readme.txt):     state 1-2, county 3-5, element 6-7, year 8-11, 12 x f7.2 from 12
Statewide record (state-readme.txt):   state 1-3, division 4 (always 0), element 5-6, year 7-10, 12 x f7.2 from 11
Normals files use the same layouts with a period code in the year field; 0010 means 1991-2020.

NCEI state codes are alphabetical (01 Alabama ... 48 Wyoming, 49 Hawaii, 50 Alaska), not FIPS.
Missing values: the readmes say -99.99 (temperature) and -9.99 (precipitation); the files actually
use -99.90 for temperature. Neither is a possible value (temperature is -50..140 F, precipitation >= 0).
"""

from __future__ import annotations

import io
import re

import polars as pl

BASE = "https://www.ncei.noaa.gov/monitoring-content/data/us/climdiv/monthly/current/"
PROCDATE_URL = BASE + "procdate.txt"
FILE_VERSION = "v1.0.0"

# series id -> (file element, element code inside the records)
ELEMENTS = {
    "tavg": ("tmpc", "02"),
    "tmax": ("tmax", "27"),
    "tmin": ("tmin", "28"),
    "pcpn": ("pcpn", "01"),
}
TEMPERATURE_SERIES = ("tavg", "tmax", "tmin")
NORMALS_1991_2020 = "0010"

# NCEI state code -> (Census FIPS, USPS), from the state code table in county-readme.txt.
NCEI_STATES = {
    "01": ("01", "AL"), "02": ("04", "AZ"), "03": ("05", "AR"), "04": ("06", "CA"), "05": ("08", "CO"),
    "06": ("09", "CT"), "07": ("10", "DE"), "08": ("12", "FL"), "09": ("13", "GA"), "10": ("16", "ID"),
    "11": ("17", "IL"), "12": ("18", "IN"), "13": ("19", "IA"), "14": ("20", "KS"), "15": ("21", "KY"),
    "16": ("22", "LA"), "17": ("23", "ME"), "18": ("24", "MD"), "19": ("25", "MA"), "20": ("26", "MI"),
    "21": ("27", "MN"), "22": ("28", "MS"), "23": ("29", "MO"), "24": ("30", "MT"), "25": ("31", "NE"),
    "26": ("32", "NV"), "27": ("33", "NH"), "28": ("34", "NJ"), "29": ("35", "NM"), "30": ("36", "NY"),
    "31": ("37", "NC"), "32": ("38", "ND"), "33": ("39", "OH"), "34": ("40", "OK"), "35": ("41", "OR"),
    "36": ("42", "PA"), "37": ("44", "RI"), "38": ("45", "SC"), "39": ("46", "SD"), "40": ("47", "TN"),
    "41": ("48", "TX"), "42": ("49", "UT"), "43": ("50", "VT"), "44": ("51", "VA"), "45": ("53", "WA"),
    "46": ("54", "WV"), "47": ("55", "WI"), "48": ("56", "WY"), "49": ("15", "HI"), "50": ("02", "AK"),
}  # fmt: skip

# County records whose code is not <NCEI state><county FIPS>. NCEI files the District of Columbia
# under Maryland as 18511; county-to-climdivs.txt in the same folder maps NCDC 18511 to FIPS 11001.
COUNTY_CODE_EXCEPTIONS = {"18511": "11001"}

# Statewide-file area codes published here; regions (101-109) and special areas (111+) are skipped.
NATIONAL_CODE = "110"  # contiguous 48 states

LAYOUTS = {
    # level: (code width, element slice start, year slice start, first value column)
    "cy": (5, 5, 7, 11),
    "st": (4, 4, 6, 10),
}
VALUE_WIDTH = 7


def procdate(body: bytes) -> str:
    text = body.decode("ascii", errors="replace").strip()
    if not re.fullmatch(r"20\d{6}", text):
        raise ValueError(f"procdate.txt should hold a YYYYMMDD date, got {text[:40]!r}")
    return text


def file_urls(date: str, level: str) -> dict[str, str]:
    """Resource name -> URL for the four elements and their normals, for 'cy' (county) or 'st' (statewide)."""
    urls: dict[str, str] = {}
    for series, (element, _code) in ELEMENTS.items():
        urls[series] = f"{BASE}climdiv-{element}{level}-{FILE_VERSION}-{date}"
        urls[f"{series}-normals"] = f"{BASE}climdiv-norm-{element}{level}-{FILE_VERSION}-{date}"
    return urls


def _lines(body: bytes) -> pl.DataFrame:
    """One string column per record, read in Rust (no Python loop over the ~400k lines)."""
    frame = pl.read_csv(
        io.BytesIO(body),
        has_header=False,
        separator="\x1f",  # never present, so each whole line is one field
        quote_char=None,
        new_columns=["line"],
        schema_overrides={"line": pl.Utf8},
    )
    return frame.with_columns(pl.col("line").str.strip_chars_end()).filter(pl.col("line") != "")


def scan(body: bytes, *, level: str, series: str) -> pl.LazyFrame:
    """Long table (code, year, month, value) of one nClimDiv file; year holds the period code in normals files.

    The record checks run eagerly and raise ValueError on malformed records or a file of the wrong element,
    so a format change fails loudly. Missing-value markers become null.
    """
    code_width, element_at, year_at, values_at = LAYOUTS[level]
    expected_element = ELEMENTS[series][1]
    width = values_at + 12 * VALUE_WIDTH
    lines = _lines(body)
    if lines.is_empty():
        raise ValueError(f"{series}: the file has no records")

    bad = lines.filter(pl.col("line").str.len_chars() != width)
    if len(bad):
        raise ValueError(f"{series}: {len(bad)} records are not {width} characters wide (e.g. {bad['line'][0]!r})")
    wide = lines.select(
        pl.col("line").str.slice(0, code_width).alias("code"),
        pl.col("line").str.slice(element_at, 2).alias("element"),
        pl.col("line").str.slice(year_at, 4).alias("year"),
        *[
            pl.col("line").str.slice(values_at + i * VALUE_WIDTH, VALUE_WIDTH).str.strip_chars().alias(str(i + 1))
            for i in range(12)
        ],
    )
    wrong = wide.filter(pl.col("element") != expected_element)
    if len(wrong):
        raise ValueError(f"{series}: expected element code {expected_element}, found {wrong['element'][0]!r}")
    if not (wide["code"].str.contains(r"^\d+$").all() and wide["year"].str.contains(r"^\d{4}$").all()):
        raise ValueError(f"{series}: non-numeric area code or year in the records")
    if wide.select(pl.struct("code", "year").is_duplicated().any()).item():
        raise ValueError(f"{series}: duplicate area/year records")

    value = pl.col("text").cast(pl.Float64, strict=True)
    missing = (value <= -99.0) if series in TEMPERATURE_SERIES else (value < 0)
    return (
        wide.lazy()
        .drop("element")
        .unpivot(index=["code", "year"], variable_name="month", value_name="text")
        .select(
            "code",
            pl.col("year").cast(pl.Int16),
            pl.col("month").cast(pl.Int8),
            pl.when(missing).then(None).otherwise(value).alias("value"),
        )
    )


def parse(body: bytes, *, level: str, series: str) -> pl.DataFrame:
    return scan(body, level=level, series=series).collect()


def build(raw: dict[str, bytes], *, level: str, location: pl.Expr) -> pl.DataFrame:
    """The report-card table for every series: value, 1991-2020 normal, anomaly and rank.

    `location` maps the `code` column to a location id; codes it maps to null (regions) are dropped.
    Rank is among all years with a value for that place, series and calendar month; 1 is the warmest
    (or wettest), and ties share the best rank. Two codes mapping to one location would surface as
    duplicate series/location/date keys, which validation blocks.
    """
    frames = []
    for series in ELEMENTS:
        values = scan(raw[series], level=level, series=series)
        normals = (
            parse(raw[f"{series}-normals"], level=level, series=series)
            .filter(pl.col("year") == int(NORMALS_1991_2020))
            .select("code", "month", pl.col("value").alias("normal"))
        )
        if normals.is_empty():
            raise ValueError(f"{series}: the normals file has no 1991-2020 (period {NORMALS_1991_2020}) records")
        places = values.select("code").unique().with_columns(location.alias("location")).drop_nulls("location")
        ym = pl.col("year").cast(pl.Int32) * 100 + pl.col("month")
        # The latest year is padded with markers for months not yet observed: drop those, keep real gaps.
        last = values.filter(pl.col("value").is_not_null()).group_by("code").agg(ym.max().alias("last"))
        frame = (
            values.join(places, on="code", how="inner")
            .join(last, on="code", how="inner")
            .filter(ym <= pl.col("last"))
            .join(normals.lazy(), on=["code", "month"], how="left")
            .select(
                pl.lit(series).alias("series"),
                "location",
                pl.format("{}-{}", "year", pl.col("month").cast(pl.Utf8).str.zfill(2)).alias("date"),
                "value",
                "normal",
                (pl.col("value") - pl.col("normal")).round(2).alias("anomaly"),
                pl.col("value")
                .rank(method="min", descending=True)
                .over(["code", "month"])
                .cast(pl.Int32)
                .alias("rank"),
            )
        )
        frames.append(frame)
    return pl.concat(frames).collect()


def county_location() -> pl.Expr:
    """'01001' -> 'us-county-01001'; NCEI state code to FIPS, with the DC exception.

    An NCEI state code outside the table raises (polars InvalidOperationError) rather than being dropped.
    """
    code = pl.col("code")
    state_fips = code.str.slice(0, 2).replace_strict({k: v[0] for k, v in NCEI_STATES.items()})
    fips = pl.when(code.is_in(list(COUNTY_CODE_EXCEPTIONS))).then(
        code.replace_strict(COUNTY_CODE_EXCEPTIONS, default=None)
    ).otherwise(pl.concat_str(state_fips, code.str.slice(2, 3)))
    return pl.concat_str(pl.lit("us-county-"), fips)


def state_location() -> pl.Expr:
    """'0010' (Alabama, division 0) -> 'us-al'; '1100' -> 'usa'; regions and special areas -> null."""
    area = pl.col("code").str.slice(0, 3)
    states = {f"0{k}": f"us-{v[1].lower()}" for k, v in NCEI_STATES.items()}
    return pl.when(pl.col("code").str.slice(3, 1) != "0").then(None).otherwise(
        area.replace_strict({**states, NATIONAL_CODE: "usa"}, default=None)
    )
