"""Validation gates. Errors block the publish (the previous version keeps serving); warnings don't."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl

from .contracts import Contract

PATTERNS = {
    "month": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "year": re.compile(r"^\d{4}$"),
    "datetime": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z$"),
}
NUMERIC = {
    "float": (pl.Float32, pl.Float64, pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64),
    "int": (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64),
}
SERIES_KEYS = ["series", "location", "date"]


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return bool(self.errors)

    @property
    def status(self) -> str:
        return "blocked" if self.errors else ("warn" if self.warnings else "pass")

    def as_dict(self) -> dict:
        return {"status": self.status, "errors": self.errors, "warnings": self.warnings}


def _columns(contract: Contract, table: pl.DataFrame, report: Report) -> bool:
    expected = contract.columns
    if sorted(table.columns) != sorted(expected):
        report.errors.append(f"columns are {table.columns}, expected {expected}")
        return False
    for col in contract.schema:
        series = table[col.name]
        nulls = series.null_count()
        if nulls and not col.nullable:
            report.errors.append(f"{col.name}: {nulls} null values in a non-nullable column")
        if col.type in PATTERNS:
            text = series.drop_nulls().cast(pl.Utf8)
            bad = text.filter(~text.str.contains(PATTERNS[col.type].pattern))
            if len(bad):
                report.errors.append(f"{col.name}: {len(bad)} values are not {col.type} (e.g. {bad[0]!r})")
        elif col.type in NUMERIC and series.dtype not in NUMERIC[col.type]:
            report.errors.append(f"{col.name}: dtype {series.dtype}, expected {col.type}")
        elif col.type == "bool" and series.dtype != pl.Boolean:
            report.errors.append(f"{col.name}: dtype {series.dtype}, expected bool")
        elif col.type == "str" and series.dtype not in (pl.Utf8, pl.Categorical, pl.Enum) and series.null_count() < len(series):
            report.errors.append(f"{col.name}: dtype {series.dtype}, expected str")
    return True


def _values(contract: Contract, table: pl.DataFrame, report: Report) -> None:
    if table.is_empty():
        report.errors.append("the table is empty")
        return
    if contract.checks.min_rows is not None and len(table) < contract.checks.min_rows:
        report.errors.append(f"{len(table)} rows, the contract requires at least {contract.checks.min_rows}")
    for name, (lo, hi) in contract.checks.ranges.items():
        values = table[name].drop_nulls()
        if values.dtype in (pl.Float32, pl.Float64):
            values = values.filter(~values.is_nan())
        bad = values.filter((values < lo) | (values > hi))
        if len(bad):
            report.errors.append(f"{name}: {len(bad)} values outside [{lo}, {hi}] (e.g. {bad[0]})")
    for name, limit in contract.checks.max_null_fraction.items():
        fraction = table[name].null_count() / len(table)
        if fraction > limit:
            report.errors.append(f"{name}: {fraction:.1%} null, limit {limit:.1%}")


def _rows_ratio(contract: Contract, table: pl.DataFrame, previous: pl.DataFrame, report: Report) -> None:
    ratio = contract.checks.rows_min_ratio
    if ratio is not None and len(table) < ratio * len(previous):
        report.errors.append(f"row count fell from {len(previous)} to {len(table)} (limit {ratio:.1%})")


def validate_series(contract: Contract, table: pl.DataFrame, previous: pl.DataFrame | None, known: dict[str, str]) -> Report:
    report = Report()
    if not _columns(contract, table, report):
        return report
    unknown_series = set(table["series"].unique()) - set(contract.series)
    if unknown_series:
        report.errors.append(f"series not declared in the contract: {sorted(unknown_series)}")
    bad_locations = sorted(loc for loc in table["location"].unique() if not contract.locations.allows(loc, known))
    if bad_locations:
        report.errors.append(f"locations not allowed by the contract: {bad_locations[:10]}{' ...' if len(bad_locations) > 10 else ''}")
    dupes = table.select(SERIES_KEYS).is_duplicated().sum()
    if dupes:
        report.errors.append(f"{dupes} rows share a series/location/date key")
    _values(contract, table, report)
    if previous is None or previous.is_empty():
        return report

    _rows_ratio(contract, table, previous, report)
    checks = contract.checks
    if checks.newest_not_older:
        now = table.group_by(["series", "location"]).agg(pl.col("date").max().alias("new_end"))
        before = previous.group_by(["series", "location"]).agg(pl.col("date").max().alias("old_end"))
        moved_back = before.join(now, on=["series", "location"], how="left").filter(
            pl.col("new_end").is_null() | (pl.col("new_end") < pl.col("old_end"))
        )
        for row in moved_back.head(10).iter_rows(named=True):
            report.errors.append(f"{row['series']}/{row['location']}: newest date went from {row['old_end']} to {row['new_end']}")
        if len(moved_back) > 10:
            report.errors.append(f"... and {len(moved_back) - 10} more series/locations whose newest date went backwards")

    gone = set(previous["location"].unique()) - set(table["location"].unique())
    if gone:
        report.warnings.append(f"{len(gone)} locations disappeared (e.g. {sorted(gone)[:5]})")

    if checks.revisions_max_changed_fraction is not None and "value" in table.columns:
        joined = previous.select([*SERIES_KEYS, pl.col("value").alias("old")]).join(
            table.select([*SERIES_KEYS, "value"]), on=SERIES_KEYS, how="inner"
        )
        if len(joined):
            changed = joined.filter((pl.col("value") - pl.col("old")).abs() > checks.revisions_tolerance)
            fraction = len(changed) / len(joined)
            if fraction > checks.revisions_max_changed_fraction:
                report.warnings.append(
                    f"{len(changed)} historical values ({fraction:.1%}) revised by more than {checks.revisions_tolerance}"
                )
    return report


def validate_table(contract: Contract, table: pl.DataFrame, previous: pl.DataFrame | None) -> Report:
    report = Report()
    if not _columns(contract, table, report):
        return report
    key = list(contract.key)
    dupes = table.select(key).is_duplicated().sum()
    if dupes:
        report.errors.append(f"{dupes} rows share a key ({', '.join(key)})")
    key_nulls = sum(table[k].null_count() for k in key)
    if key_nulls:
        report.errors.append(f"{key_nulls} null values in key columns")
    if contract.partition and table[contract.partition].null_count():
        report.errors.append(f"partition column {contract.partition} has null values")
    _values(contract, table, report)
    if previous is not None and not previous.is_empty():
        _rows_ratio(contract, table, previous, report)
    return report


def validate_files(contract: Contract, paths: set[str], stats: dict[str, float]) -> Report:
    report = Report()
    missing = [p for p in contract.checks.required_files if p not in paths]
    if missing:
        report.errors.append(f"required files missing: {missing}")
    if contract.checks.min_rows is not None and stats.get("rows", 0) < contract.checks.min_rows:
        report.errors.append(f"{stats.get('rows', 0)} features/rows, the contract requires at least {contract.checks.min_rows}")
    return report


def validate(contract: Contract, table: pl.DataFrame, previous: pl.DataFrame | None, known: dict[str, str] | None = None) -> Report:
    """Series or table validation (files datasets use validate_files)."""
    if contract.kind == "table":
        return validate_table(contract, table, previous)
    return validate_series(contract, table, previous, contract.locations.names() if known is None else known)
