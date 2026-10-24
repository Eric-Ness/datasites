"""carbondata/national-fossil-co2: annual fossil CO2 by country, from the Global Carbon Budget.

Source: the Global Carbon Project's fossil CO2 emissions dataset on Zenodo (concept record 5569234;
the 2025 release is record 17417124, file GCB2025v15_MtCO2_flat.csv), CC BY 4.0. Each run asks
Zenodo for the newest version, so next November's release is picked up without a code change. The
raw resource is named after the release file, so every published version records which release it
came from.

Columns used: Country, ISO 3166-1 alpha-3, Year, and the emissions Total, Coal, Oil, Gas, Cement,
Flaring and Other (million tonnes CO2), one series each. GCB's Per Capita column is left out: in the
2025v15 file it is off by about a factor of 1,000 for the Netherlands in 1900-1949 (up to 5,620
tonnes per person), and a per-capita figure depends on a population series this dataset does not carry.

Locations: GCB's "Global" is `world`; countries use the geo registry's ISO alpha-3 ids. GCB also
reports entities that are not countries, or not in ISO 3166. They are kept as explicit locations so
that, as in the source, every entity's emissions add up to the world total:
    gcb-international-aviation, gcb-international-shipping   bunker fuels, outside national totals
    gcb-kuwaiti-oil-fires                                     non-zero in 1991 only
    gcb-kosovo                                                code KSV is not ISO 3166
    gcb-pacific-islands-palau, gcb-ryukyu-islands             historical territories
An entity GCB adds in a later release gets a gcb-<name> id that the contract does not list, so
validation blocks that version until the contract names it.
"""

from __future__ import annotations

import io
import re

import polars as pl

LATEST = "https://zenodo.org/api/records/17417124/versions/latest"
FILE = re.compile(r"^GCB\d{4}v\d+_MtCO2_flat\.csv$")
SERIES = {"Total": "total", "Coal": "coal", "Oil": "oil", "Gas": "gas", "Cement": "cement", "Flaring": "flaring", "Other": "other"}
SPECIAL = {
    "Global": "world",
    "International Aviation": "gcb-international-aviation",
    "International Shipping": "gcb-international-shipping",
    "Kuwaiti Oil Fires": "gcb-kuwaiti-oil-fires",
    "Kosovo": "gcb-kosovo",
    "Pacific Islands (Palau)": "gcb-pacific-islands-palau",
    "Ryukyu Islands": "gcb-ryukyu-islands",
}
COUNTRY, CODE, YEAR = "Country", "ISO 3166-1 alpha-3", "Year"


def latest_file(record: dict) -> tuple[str, str]:
    """(file name, download URL) of the MtCO2 flat CSV in a Zenodo record document."""
    found = [(f["key"], f["links"]["self"]) for f in record.get("files", []) if FILE.match(f.get("key", ""))]
    if len(found) != 1:
        raise ValueError(f"expected one GCB*_MtCO2_flat.csv in Zenodo record {record.get('id')}, found {[k for k, _ in found]}")
    return found[0]


def resources(ctx) -> dict[str, str]:
    name, url = latest_file(ctx.get_json(LATEST))
    return {name: url}


def _location(name: str, code: str | None) -> str:
    if name in SPECIAL:
        return SPECIAL[name]
    if code:
        return code.strip().lower()
    return "gcb-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    if len(raw) != 1:
        raise ValueError(f"expected one GCB file, got {sorted(raw)}")
    (body,) = raw.values()
    table = pl.read_csv(io.BytesIO(body), infer_schema_length=0)
    missing = [c for c in [COUNTRY, CODE, YEAR, *SERIES] if c not in table.columns]
    if missing:
        raise ValueError(f"GCB file is missing columns {missing}")

    entities = table.select(COUNTRY, CODE).unique()
    locations = {name: _location(name, code) for name, code in entities.iter_rows()}
    table = table.with_columns(
        pl.col(COUNTRY).replace_strict(locations, return_dtype=pl.Utf8).alias("location"),
        pl.col(YEAR).str.strip_chars().alias("date"),
    )
    frames = [
        table.select(pl.lit(series).alias("series"), "location", "date", pl.col(column).cast(pl.Float64).alias("value"))
        for column, series in SERIES.items()
    ]
    return pl.concat(frames).filter(pl.col("value").is_not_null())
