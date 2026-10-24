"""The emission-factor table every source is mapped into, and the vocabularies that keep it honest.

One row is one published number: `value` kilograms of `gas` (or of CO2e) per one `activity_unit`.

- Per-gas mass factors (kg CO2, kg CH4, kg N2O per unit) carry gwp_set "none".
- CO2e factors name the IPCC assessment report whose 100-year GWPs the publisher used
  ("AR4", "AR5", "AR6"), or "mixed" when the publisher combines several. Values are never converted
  from one GWP set to another, and every CO2e row says which set it uses.
- gas "CO2e" means all gases the factor covers, already weighted. A named gas with a CO2e unit
  (UK DESNZ publishes these) is that gas's share of the total, so per-gas rows and their CO2e total
  must not be summed.
- scope is "1", "2", "3", or "outside" for biogenic CO2 that GHG Protocol inventories report
  outside the scopes.
"""

from __future__ import annotations

import math
import re
import unicodedata
from functools import cache

import polars as pl

from datasites_core import geo

COLUMNS = [
    "factor_id",
    "source",
    "source_version",
    "source_ref",
    "scope",
    "category",
    "activity",
    "region",
    "region_type",
    "activity_unit",
    "gas",
    "value",
    "unit",
    "gwp_set",
    "valid_year",
    "notes",
]

# What each source module returns: COLUMNS without the two derived ones, plus `edition`, the short
# release token used in factor_id ("2026" for the Hub 2026 release, the data year for Ember).
BUILD_COLUMNS = [c for c in COLUMNS if c not in ("factor_id", "unit")] + ["edition"]

SOURCES = ("epa-hub", "egrid", "uk-desnz", "ember", "useeio")
SCOPES = ("1", "2", "3", "outside")
GASES = ("CO2", "CH4", "N2O", "CO2e")
GWP_SETS = ("AR4", "AR5", "AR6", "mixed", "none")
REGION_TYPES = ("world", "country", "us-state", "egrid-subregion")
CATEGORIES = (
    "stationary-combustion",
    "mobile-combustion",
    "fuels",
    "refrigerants-and-process-gases",
    "electricity",
    "heat-and-steam",
    "transmission-and-distribution",
    "well-to-tank",
    "freight",
    "business-travel",
    "homeworking",
    "water",
    "materials",
    "waste",
    "purchased-goods-and-services",
)
# Activity units, spelled one way across sources. Energy units carry their heating-value basis when
# the publisher states one: the Hub's mmBtu are higher heating value; DESNZ gives net and gross kWh.
ACTIVITY_UNITS = (
    "mmBtu",
    "GJ",
    "kWh",
    "kWh (net CV)",
    "kWh (gross CV)",
    "MWh",
    "short ton",
    "tonne",
    "kg",
    "gallon",
    "litre",
    "million litres",
    "scf",
    "m3",
    "vehicle-mile",
    "vehicle-km",
    "passenger-mile",
    "passenger-km",
    "short ton-mile",
    "tonne-km",
    "room-night",
    "FTE working hour",
    "USD (2024, purchaser price)",
)

_SYMBOLS = [("≤", " le "), ("≥", " ge "), ("<=", " le "), (">=", " ge "), ("<", " lt "), (">", " gt "), ("%", " pct "), ("+", " plus "), ("&", " and ")]


def slug(text: str) -> str:
    """'Air Travel - Short Haul (< 300 miles)' -> 'air-travel-short-haul-lt-300-miles'."""
    for symbol, word in _SYMBOLS:
        text = text.replace(symbol, word)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean(text: str | None) -> str | None:
    """Collapses runs of whitespace (cells often end in stray spaces or line breaks)."""
    if text is None:
        return None
    text = " ".join(text.split())
    return text or None


def join_activity(parts: list[str | None]) -> str:
    """'Hotel stay', 'Hotel stay', 'UK' -> 'Hotel stay / UK': blank parts and repeated labels dropped."""
    kept: list[str] = []
    for part in parts:
        if part and (not kept or kept[-1] != part):
            kept.append(part)
    if not kept:
        raise ValueError("empty activity")
    return " / ".join(kept)


def number(text: str | None) -> float | None:
    """A spreadsheet cell as a float; blank cells are None (no factor published)."""
    text = clean(text)
    if text is None:
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite factor value {text!r}")
    return value


@cache
def _registry() -> dict[str, dict]:
    return geo.load()


@cache
def _names() -> dict[str, str]:
    return {loc["name"].lower(): loc_id for loc_id, loc in _registry().items() if loc["level"] == "country"}


def country_from_iso3(code: str) -> str:
    loc_id = code.strip().lower()
    if _registry().get(loc_id, {}).get("level") != "country":
        raise ValueError(f"{code!r} is not an ISO 3166-1 alpha-3 country in the geo registry")
    return loc_id


def country_from_name(name: str, aliases: dict[str, str]) -> str:
    """A country id from a publisher's country name: its alias table first, then registry names."""
    if name in aliases:
        return aliases[name]
    found = _names().get(name.strip().lower())
    if not found:
        raise ValueError(f"unknown country name {name!r}; add it to the source's alias table")
    return found


def rows_frame(rows: list[dict]) -> pl.DataFrame:
    """Builds a source's frame from row dicts with the BUILD_COLUMNS layout and types."""
    schema = {c: pl.Utf8 for c in BUILD_COLUMNS}
    schema["value"] = pl.Float64
    return pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)


def _check_vocab(frame: pl.DataFrame, column: str, allowed: tuple[str, ...]) -> None:
    bad = sorted(set(frame[column].drop_nulls().unique().to_list()) - set(allowed))
    if bad or frame[column].null_count():
        raise ValueError(f"{column}: unexpected values {bad[:10]} (or nulls); allowed {allowed}")


def finish(frame: pl.DataFrame) -> pl.DataFrame:
    """Checks the vocabularies, derives `unit` and `factor_id`, and returns the contract's columns."""
    missing = [c for c in BUILD_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"source frame is missing columns {missing}")
    for column, allowed in (
        ("source", SOURCES),
        ("scope", SCOPES),
        ("category", CATEGORIES),
        ("region_type", REGION_TYPES),
        ("activity_unit", ACTIVITY_UNITS),
        ("gas", GASES),
        ("gwp_set", GWP_SETS),
    ):
        _check_vocab(frame, column, allowed)

    mass_co2e = frame.filter((pl.col("gas") == "CO2e") & (pl.col("gwp_set") == "none"))
    if len(mass_co2e):
        raise ValueError(f"{len(mass_co2e)} CO2e rows have no GWP set (e.g. {mass_co2e.row(0, named=True)})")
    bad_values = frame.filter(pl.col("value").is_null() | pl.col("value").is_nan() | (pl.col("value") < 0))
    if len(bad_values):
        raise ValueError(f"{len(bad_values)} rows have a missing or negative value (e.g. {bad_values.row(0, named=True)})")
    bad_years = frame.filter(~pl.col("valid_year").str.contains(r"^\d{4}$"))
    if len(bad_years):
        raise ValueError(f"{len(bad_years)} rows have a malformed valid_year (e.g. {bad_years.row(0, named=True)})")

    registry = _registry()
    for region, region_type in frame.select("region", "region_type").unique().iter_rows():
        if region_type == "egrid-subregion":
            ok = bool(re.fullmatch(r"[A-Z]{4}", region))
        else:
            ok = registry.get(region, {}).get("level") == region_type
        if not ok:
            raise ValueError(f"region {region!r} is not a valid {region_type} id")

    slugs = {text: slug(text) for text in frame["activity"].unique().to_list() + list(ACTIVITY_UNITS)}
    gas_word = pl.when(pl.col("gwp_set") == "none").then(pl.col("gas")).otherwise(pl.lit("CO2e"))
    return frame.with_columns(
        pl.concat_str(
            [
                pl.col("source"),
                pl.col("edition"),
                pl.col("category"),
                pl.col("activity").replace_strict(slugs, return_dtype=pl.Utf8),
                pl.col("region").str.to_lowercase(),
                pl.col("gas").str.to_lowercase(),
                pl.col("activity_unit").replace_strict(slugs, return_dtype=pl.Utf8),
            ],
            separator=":",
        ).alias("factor_id"),
        pl.concat_str([pl.lit("kg "), gas_word, pl.lit("/"), pl.col("activity_unit")]).alias("unit"),
    ).select(COLUMNS)
