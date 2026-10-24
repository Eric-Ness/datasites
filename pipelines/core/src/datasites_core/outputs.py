"""Turns a pipeline's long table into the published files. Output is deterministic: same table, same bytes."""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from typing import Any

import polars as pl

from .contracts import Contract

JSON_TYPE = "application/json"
CSV_TYPE = "text/csv; charset=utf-8"
PARQUET_TYPE = "application/vnd.apache.parquet"


@dataclass(frozen=True)
class Output:
    body: bytes
    content_type: str
    rows: int


def _clean(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _csv(columns: list[str], rows: list[tuple]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(_clean(v)) for v in row])
    return buf.getvalue().encode()


def sort_table(contract: Contract, table: pl.DataFrame) -> pl.DataFrame:
    return table.select(contract.columns).sort(["series", "location", "date"])


def build_outputs(contract: Contract, table: pl.DataFrame) -> dict[str, Output]:
    """{'average/noaa-mlo.json': ..., 'average/noaa-mlo.csv': ..., 'download/<id>.csv': ..., 'download/<id>.parquet': ...}"""
    table = sort_table(contract, table)
    value_columns = [c.name for c in contract.schema]
    units = {c.name: c.unit for c in contract.schema if c.unit}
    outputs: dict[str, Output] = {}

    for (series, location), group in table.group_by(["series", "location"], maintain_order=True):
        rows = group.select(value_columns).rows()
        meta = {
            "dataset": contract.id,
            "series": series,
            "series_name": contract.series[series],
            "location": location,
            "location_name": contract.locations[location],
            "units": units,
            "source": contract.source.name,
            "license": contract.source.license,
            "attribution": contract.source.attribution,
        }
        data = [{col: _clean(v) for col, v in zip(value_columns, row)} for row in rows]
        body = json.dumps({"meta": meta, "data": data}, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        outputs[f"{series}/{location}.json"] = Output(body.encode(), JSON_TYPE, len(rows))
        outputs[f"{series}/{location}.csv"] = Output(_csv(value_columns, rows), CSV_TYPE, len(rows))

    all_rows = table.rows()
    outputs[f"download/{contract.id}.csv"] = Output(_csv(contract.columns, all_rows), CSV_TYPE, len(all_rows))

    buf = io.BytesIO()
    table.write_parquet(buf, compression="zstd", statistics=True)
    outputs[f"download/{contract.id}.parquet"] = Output(buf.getvalue(), PARQUET_TYPE, len(all_rows))
    return outputs


def coverage(table: pl.DataFrame) -> dict[str, str | None]:
    if table.is_empty():
        return {"start": None, "end": None}
    return {"start": table["date"].min(), "end": table["date"].max()}
