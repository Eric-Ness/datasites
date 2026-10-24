"""The interface a state adapter implements, and the shared step that turns its rows into county_schema rows.

An adapter knows one state's official results files: where they are for a given election date
(resources), and how to read them (parse). parse returns
one row per county, contest, ballot line and vote mode in PARSED_COLUMNS. to_county_rows() checks the
rows and finishes them: registry ids, simplified party, totalvotes, and the state-level columns. It
keeps each row's resource name so the importer can link the row to the exact file it came from.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

import polars as pl

from .. import county_schema

PARSED_COLUMNS = {
    "resource": pl.Utf8,  # which of resources() the row came from; becomes source_url
    "county_fips": pl.Utf8,
    "county_name": pl.Utf8,
    "office": pl.Utf8,
    "district": pl.Utf8,
    "candidate": pl.Utf8,
    "party_detailed": pl.Utf8,
    "writein": pl.Boolean,
    "mode": pl.Utf8,
    "candidatevotes": pl.Int64,
}
CONTEST = ["office", "district"]
LINE = ["office", "district", "county_name", "candidate", "party_detailed"]


@dataclass(frozen=True)
class SourceInfo:
    """What a contract's source block says about a state's results files, from the state's own pages."""

    id: str
    name: str
    publisher: str
    url: str  # the page that documents the files
    terms_url: str  # the page the license and commercial_ok come from
    license: str
    commercial_ok: bool | None
    attribution: str


class StateAdapter(ABC):
    state_po: ClassVar[str]
    source: ClassVar[SourceInfo]
    # False for a state whose files report by units that aren't counties (Alaska reports by state house
    # district). Its rows then have a null county_fips, and the every-county completeness check is skipped.
    reports_by_county: ClassVar[bool] = True

    @abstractmethod
    def resources(self, election_date: date) -> dict[str, str]:
        """name -> URL of the state's official results files for that election. Each row's source_url is one of these."""

    @abstractmethod
    def parse(self, raw: dict[str, bytes]) -> pl.DataFrame:
        """Federal and statewide contests only, in PARSED_COLUMNS. Raise on anything unexpected."""


def decode(body: bytes) -> str:
    """UTF-8 (with or without a byte-order mark); Windows-1252 for files that aren't UTF-8."""
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("cp1252")


def whole_votes(values: pl.Series, what: str) -> pl.Series:
    text = values.str.strip_chars().str.replace_all(",", "")
    bad = text.filter(~text.str.contains(r"^\d+$"))
    if len(bad):
        raise ValueError(f"{what}: vote counts that are not whole numbers: {bad.unique().to_list()[:5]}")
    return text.cast(pl.Int64)


def to_county_rows(adapter: StateAdapter, raw: dict[str, bytes], year: int, *, stage: str, special: set[tuple[str, str]]) -> pl.DataFrame:
    """The adapter's rows as county_schema rows, after checking them against the registry and each other."""
    state = adapter.state_po
    what = f"{state} results"
    rows = adapter.parse(raw)
    if dict(rows.schema) != PARSED_COLUMNS:
        raise TypeError(f"{what}: parse() returned {dict(rows.schema)}, expected {PARSED_COLUMNS}")
    if rows.is_empty():
        raise ValueError(f"{what}: no federal or statewide contests found")
    for column in ("resource", "county_name", "office", "district", "candidate", "party_detailed", "writein", "mode", "candidatevotes"):
        if rows[column].null_count():
            raise ValueError(f"{what}: null {column} values")
    bad_modes = sorted(set(rows["mode"].unique().to_list()) - set(county_schema.MODES))
    if bad_modes:
        raise ValueError(f"{what}: unknown vote modes {bad_modes}")
    bad_districts = rows.filter(~pl.col("district").str.contains(r"^(STATEWIDE|\d{3})$"))
    if len(bad_districts):
        raise ValueError(f"{what}: bad district values {bad_districts['district'].unique().to_list()[:5]}")

    registry = county_schema.counties(state)
    fips = rows["county_fips"].drop_nulls().unique().to_list()
    unknown = sorted(set(fips) - set(registry))
    if unknown:
        raise ValueError(f"{what}: county FIPS codes not in the registry for {state}: {unknown}")
    if adapter.reports_by_county:
        if rows["county_fips"].null_count():
            raise ValueError(f"{what}: rows without a county FIPS code, e.g. {rows.filter(pl.col('county_fips').is_null()).row(0, named=True)}")
        statewide = rows.filter((pl.col("district") == "STATEWIDE") & (pl.col("mode") == "TOTAL"))
        for (office,), group in statewide.partition_by("office", as_dict=True).items():
            missing = sorted(set(registry) - set(group["county_fips"].to_list()))
            if missing:
                raise ValueError(f"{what}: {office} has no results for counties {missing} (an incomplete file?)")

    key = [*LINE, "mode"]
    dupes = rows.filter(rows.select(key).is_duplicated())
    if len(dupes):
        raise ValueError(f"{what}: {len(dupes)} rows repeat a county/contest/ballot line/mode, e.g. {dupes.row(0, named=True)}")
    totals = rows.filter(pl.col("mode") == "TOTAL")
    parts = rows.filter(pl.col("mode") != "TOTAL").group_by(LINE).agg(pl.col("candidatevotes").sum().alias("parts"))
    lines_without_total = parts.join(totals, on=LINE, how="anti")
    if len(lines_without_total):
        raise ValueError(f"{what}: vote-mode rows without a TOTAL row, e.g. {lines_without_total.row(0, named=True)}")
    mismatched = parts.join(totals, on=LINE).filter(pl.col("parts") != pl.col("candidatevotes"))
    if len(mismatched):
        raise ValueError(f"{what}: vote modes don't add up to the TOTAL, e.g. {mismatched.row(0, named=True)}")

    nonpartisan = (pl.col("party_detailed") == "") & ~pl.col("writein")
    simplified = {label: county_schema.simplify_party(label) for label in rows["party_detailed"].unique().to_list()}
    special_expr = pl.lit(False)
    for office, district in sorted(special):
        special_expr = special_expr | ((pl.col("office") == office) & (pl.col("district") == district))
    out = rows.with_columns(
        pl.lit(str(year)).alias("year"),
        pl.lit(county_schema.state_name(state)).alias("state"),
        pl.lit(state).alias("state_po"),
        pl.when(pl.col("county_fips").is_null()).then(None).otherwise(pl.lit("us-county-") + pl.col("county_fips")).alias("county_id"),
        pl.lit(stage).alias("stage"),
        special_expr.alias("special"),
        pl.when(nonpartisan)
        .then(pl.lit("NONPARTISAN"))
        .otherwise(pl.col("party_detailed").replace_strict(simplified, return_dtype=pl.Utf8))
        .alias("party"),
        pl.col("candidatevotes").sum().over([*CONTEST, "county_name", "mode"]).alias("totalvotes"),
    )
    return county_schema.finish(out).with_columns(out["resource"])


def office_number(text: str) -> int:
    """'#02' -> 2, '07' -> 7."""
    m = re.fullmatch(r"#?0*(\d+)", text.strip())
    if not m:
        raise ValueError(f"not a district or position number: {text!r}")
    return int(m.group(1))
