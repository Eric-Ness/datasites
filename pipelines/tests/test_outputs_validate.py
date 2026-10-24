import io
import json

import polars as pl

from datasites_climate.noaa_gml_co2 import transform
from datasites_core.outputs import build_series_outputs
from datasites_core.validate import validate


def test_outputs_are_deterministic(contract, raw):
    first = build_series_outputs(contract, transform(raw), contract.locations.names())
    second = build_series_outputs(contract, transform(raw).reverse(), contract.locations.names())  # row order must not matter
    assert first.keys() == second.keys()
    for path in first:
        assert first[path].body == second[path].body, path


def test_output_files(contract, raw):
    out = build_series_outputs(contract, transform(raw), contract.locations.names())
    assert sorted(out) == [
        "average/noaa-mlo.csv",
        "average/noaa-mlo.json",
        "average/world.csv",
        "average/world.json",
        "download/co2-monthly.csv",
        "download/co2-monthly.parquet",
        "trend/noaa-mlo.csv",
        "trend/noaa-mlo.json",
        "trend/world.csv",
        "trend/world.json",
    ]

    doc = json.loads(out["average/noaa-mlo.json"].body)
    assert doc["meta"]["units"] == {"value": "ppm", "uncertainty": "ppm"}
    assert doc["meta"]["license"] == "LicenseRef-US-Government-Work"
    assert doc["data"][0] == {"date": "1958-03", "value": 315.71, "uncertainty": None, "interpolated": None}
    assert doc["data"][-1]["date"] == "2026-09"

    csv_lines = out["average/noaa-mlo.csv"].body.decode().splitlines()
    assert csv_lines[0] == "date,value,uncertainty,interpolated"
    assert csv_lines[1] == "1958-03,315.71,,"
    assert "1975-12,330.77,,true" in csv_lines
    assert csv_lines[-1] == "2026-09,426.07,0.23,false"

    bulk = pl.read_parquet(io.BytesIO(out["download/co2-monthly.parquet"].body))
    assert bulk.columns == contract.columns
    assert len(bulk) == out["download/co2-monthly.csv"].rows == 32


def test_valid_table_passes(contract, raw):
    table = transform(raw)
    report = validate(contract, table, previous=table)
    assert report.status == "pass", report.errors + report.warnings


def test_out_of_range_value_blocks(contract, raw):
    table = transform(raw).with_columns(
        pl.when(pl.col("date") == "2026-09").then(4260.7).otherwise(pl.col("value")).alias("value")
    )
    report = validate(contract, table, previous=None)
    assert report.blocking
    assert any("outside [250.0, 600.0]" in e for e in report.errors)


def test_missing_values_block(contract, raw):
    table = transform(raw).with_columns(
        pl.when(pl.col("date") == "1979-01").then(None).otherwise(pl.col("value")).alias("value")
    )
    assert any("value:" in e and "null" in e for e in validate(contract, table, previous=None).errors)


def test_shrinking_table_blocks(contract, raw):
    full = transform(raw)
    report = validate(contract, full.head(10), previous=full)
    assert any("row count fell" in e for e in report.errors)
    assert any("newest date went from" in e for e in report.errors)


def test_unknown_location_and_duplicates_block(contract, raw):
    table = transform(raw)
    stray = table.head(1).with_columns(pl.lit("mars").alias("location"))
    report = validate(contract, pl.concat([table, stray, table.head(1)]), previous=None)
    assert any("locations not allowed" in e for e in report.errors)
    assert any("share a series/location/date key" in e for e in report.errors)


def test_bad_month_format_blocks(contract, raw):
    table = transform(raw).with_columns(pl.col("date").str.replace("-", "/"))
    assert any("not month" in e for e in validate(contract, table, previous=None).errors)


def test_large_revisions_only_warn(contract, raw):
    table = transform(raw)
    revised = table.with_columns((pl.col("value") + 1.0).alias("value"))
    report = validate(contract, revised, previous=table)
    assert not report.blocking
    assert any("revised by more than" in w for w in report.warnings)
