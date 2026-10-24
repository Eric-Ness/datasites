"""Validation gates. Errors block the publish (the previous version keeps serving); warnings don't."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl

from .contracts import Contract

PATTERNS = {"month": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"), "date": re.compile(r"^\d{4}-\d{2}-\d{2}$")}
NUMERIC = {"float": (pl.Float32, pl.Float64, pl.Int32, pl.Int64), "int": (pl.Int8, pl.Int16, pl.Int32, pl.Int64)}
KEYS = ["series", "location", "date"]


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


def _schema(contract: Contract, table: pl.DataFrame, report: Report) -> bool:
    expected = contract.columns
    if table.columns != expected and sorted(table.columns) != sorted(expected):
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
    unknown_series = set(table["series"].unique()) - set(contract.series)
    unknown_locations = set(table["location"].unique()) - set(contract.locations)
    if unknown_series:
        report.errors.append(f"series not declared in the contract: {sorted(unknown_series)}")
    if unknown_locations:
        report.errors.append(f"locations not declared in the contract: {sorted(unknown_locations)}")
    dupes = table.select(KEYS).is_duplicated().sum()
    if dupes:
        report.errors.append(f"{dupes} rows share a series/location/date key")
    return True


def _values(contract: Contract, table: pl.DataFrame, report: Report) -> None:
    if table.is_empty():
        report.errors.append("the table is empty")
        return
    for name, (lo, hi) in contract.checks.ranges.items():
        values = table[name].drop_nulls()
        bad = values.filter((values < lo) | (values > hi))
        if len(bad):
            report.errors.append(f"{name}: {len(bad)} values outside [{lo}, {hi}] (e.g. {bad[0]})")
    for name, limit in contract.checks.max_null_fraction.items():
        fraction = table[name].null_count() / len(table)
        if fraction > limit:
            report.errors.append(f"{name}: {fraction:.1%} null, limit {limit:.1%}")


def _against_previous(contract: Contract, table: pl.DataFrame, previous: pl.DataFrame, report: Report) -> None:
    checks = contract.checks
    if checks.rows_min_ratio is not None and len(table) < checks.rows_min_ratio * len(previous):
        report.errors.append(f"row count fell from {len(previous)} to {len(table)} (limit {checks.rows_min_ratio:.1%})")

    if checks.newest_not_older:
        now = table.group_by(["series", "location"]).agg(pl.col("date").max().alias("new_end"))
        before = previous.group_by(["series", "location"]).agg(pl.col("date").max().alias("old_end"))
        moved_back = before.join(now, on=["series", "location"], how="left").filter(
            pl.col("new_end").is_null() | (pl.col("new_end") < pl.col("old_end"))
        )
        for row in moved_back.iter_rows(named=True):
            report.errors.append(f"{row['series']}/{row['location']}: newest date went from {row['old_end']} to {row['new_end']}")

    gone = set(previous["location"].unique()) - set(table["location"].unique())
    if gone:
        report.warnings.append(f"locations disappeared: {sorted(gone)}")

    if checks.revisions_max_changed_fraction is not None and "value" in table.columns:
        joined = previous.select([*KEYS, pl.col("value").alias("old")]).join(table.select([*KEYS, "value"]), on=KEYS, how="inner")
        if len(joined):
            changed = joined.filter((pl.col("value") - pl.col("old")).abs() > checks.revisions_tolerance)
            fraction = len(changed) / len(joined)
            if fraction > checks.revisions_max_changed_fraction:
                report.warnings.append(
                    f"{len(changed)} historical values ({fraction:.1%}) revised by more than {checks.revisions_tolerance}"
                )


def validate(contract: Contract, table: pl.DataFrame, previous: pl.DataFrame | None) -> Report:
    report = Report()
    if not _schema(contract, table, report):
        return report
    _values(contract, table, report)
    if previous is not None and not previous.is_empty():
        _against_previous(contract, table, previous, report)
    return report
