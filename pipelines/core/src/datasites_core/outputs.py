"""Turns a pipeline's table into the published files. Output is deterministic: same table, same bytes.

series  <series>/<location>.json|csv per place, plus download/<id>.csv|parquet
table   table/<partition>.json|csv per partition (and table/all.* when small), plus download/<id>.csv|parquet
files   whatever the pipeline built (see publish.py)
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import polars as pl

from .contracts import Contract

JSON_TYPE = "application/json"
CSV_TYPE = "text/csv; charset=utf-8"
PARQUET_TYPE = "application/vnd.apache.parquet"
TABLE_ALL_MAX_ROWS = 50_000  # above this, table/all.* is skipped and clients use download/ or partitions

CONTENT_TYPES = {
    ".json": JSON_TYPE,
    ".geojson": "application/geo+json",
    ".topojson": JSON_TYPE,
    ".csv": CSV_TYPE,
    ".parquet": PARQUET_TYPE,
    ".pmtiles": "application/vnd.pmtiles",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}


@dataclass(frozen=True)
class Output:
    body: bytes
    content_type: str
    rows: int


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)


def _json_rows(df: pl.DataFrame) -> str:
    """Row-oriented JSON array; NaN becomes null."""
    floats = [c for c, t in df.schema.items() if t in (pl.Float32, pl.Float64)]
    if floats:
        df = df.with_columns(pl.col(floats).fill_nan(None))
    return df.write_json() if len(df) else "[]"


def _csv(df: pl.DataFrame) -> bytes:
    return df.write_csv(line_terminator="\n").encode()


def _parquet(df: pl.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd", statistics=True)
    return buf.getvalue()


def _source_meta(contract: Contract) -> dict:
    return {
        "dataset": contract.id,
        "source": contract.source.name,
        "license": contract.source.license,
        "attribution": contract.source.attribution,
    }


def sort_table(contract: Contract, table: pl.DataFrame) -> pl.DataFrame:
    if contract.kind == "series":
        return table.select(contract.columns).sort(["series", "location", "date"])
    return table.select(contract.columns).sort(list(contract.key))


def build_series_outputs(contract: Contract, table: pl.DataFrame, location_names: dict[str, str]) -> dict[str, Output]:
    table = sort_table(contract, table)
    value_columns = [c.name for c in contract.schema]
    units = {c.name: c.unit for c in contract.schema if c.unit}
    outputs: dict[str, Output] = {}
    for (series, location), group in table.partition_by(["series", "location"], as_dict=True, maintain_order=True).items():
        values = group.select(value_columns)
        meta = {
            **_source_meta(contract),
            "series": series,
            "series_name": contract.series[series],
            "location": location,
            "location_name": location_names.get(location, location),
            "units": units,
        }
        body = '{"meta":' + _dumps(meta) + ',"data":' + _json_rows(values) + "}"
        outputs[f"{series}/{location}.json"] = Output(body.encode(), JSON_TYPE, len(values))
        outputs[f"{series}/{location}.csv"] = Output(_csv(values), CSV_TYPE, len(values))
    outputs[f"download/{contract.id}.csv"] = Output(_csv(table), CSV_TYPE, len(table))
    outputs[f"download/{contract.id}.parquet"] = Output(_parquet(table), PARQUET_TYPE, len(table))
    return outputs


def build_table_outputs(contract: Contract, table: pl.DataFrame) -> dict[str, Output]:
    table = sort_table(contract, table)
    units = {c.name: c.unit for c in contract.schema if c.unit}
    meta = {**_source_meta(contract), "key": list(contract.key), "units": units}
    outputs: dict[str, Output] = {}

    def add(name: str, rows: pl.DataFrame, extra: dict) -> None:
        body = '{"meta":' + _dumps({**meta, **extra, "rows": len(rows)}) + ',"rows":' + _json_rows(rows) + "}"
        outputs[f"table/{name}.json"] = Output(body.encode(), JSON_TYPE, len(rows))
        outputs[f"table/{name}.csv"] = Output(_csv(rows), CSV_TYPE, len(rows))

    if contract.partition:
        for (value,), group in table.partition_by(contract.partition, as_dict=True, maintain_order=True).items():
            add(str(value), group, {"partition": {contract.partition: value}})
    if len(table) <= TABLE_ALL_MAX_ROWS:
        add("all", table, {})
    outputs[f"download/{contract.id}.csv"] = Output(_csv(table), CSV_TYPE, len(table))
    outputs[f"download/{contract.id}.parquet"] = Output(_parquet(table), PARQUET_TYPE, len(table))
    return outputs


def content_type_for(path: str) -> str:
    for ext, ctype in CONTENT_TYPES.items():
        if path.endswith(ext):
            return ctype
    return "application/octet-stream"


def coverage(contract: Contract, table: pl.DataFrame | None) -> dict[str, str | None]:
    column = contract.time_column
    if table is None or table.is_empty() or column is None or column not in table.columns:
        return {"start": None, "end": None}
    values = table[column].drop_nulls().cast(pl.Utf8)
    if values.is_empty():
        return {"start": None, "end": None}
    return {"start": values.min(), "end": values.max()}
