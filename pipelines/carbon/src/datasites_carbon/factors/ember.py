"""Ember yearly electricity data: national grid emissions intensity, every country and the world.

Source file: release_generation_yearly_global.csv (the keyless download on Ember's Yearly
Electricity Data page; layout of July 2026: one row per area, year and electricity source, with
metrics as columns). The factor is the "Total generation" row's "Emissions intensity (gCO2e/kWh)",
converted to kg CO2e per kWh.

What a GHG accountant needs to know: Ember's intensity is a lifecycle figure. Its methodology says
the emissions include upstream methane, supply chain and manufacturing, for all gases as CO2e, with
upstream methane weighted at GWP 21. So gwp_set is "mixed", and it overlaps the Scope 3 Category 3
(upstream) emissions that combustion-only Scope 2 factors such as eGRID or DESNZ leave out.

Left out, with reasons: Ember's regional aggregates other than the world (EU, OECD, G20, continents:
their membership is Ember's own grouping, not a region code); Kosovo (XKX is not an ISO 3166 code,
so the geo registry has no id for it); blank intensities (no generation that year); and negative
intensities (Costa Rica 2025 in the October 2026 file, an estimation artefact of negative fossil
generation; a grid cannot have negative emissions).
"""

from __future__ import annotations

import io

import polars as pl

from .common import country_from_iso3, rows_frame

SOURCE = "ember"
INTENSITY = "Emissions intensity (gCO2e/kWh)"
REQUIRED = ["Area", "ISO 3 code", "Year", "Area type", "Electricity source", INTENSITY]
NOT_IN_REGISTRY = {"XKX"}  # Kosovo
ACTIVITY = "Grid electricity, lifecycle intensity of generation"
NOTE = (
    "Lifecycle intensity of the country's generation: combustion plus upstream methane, supply chain and "
    "manufacturing, all gases as CO2e (methane at GWP 21). Not combustion-only, so it overlaps Scope 3 "
    "Category 3; use where no national Scope 2 grid factor exists."
)


def factors(body: bytes) -> pl.DataFrame:
    table = pl.read_csv(io.BytesIO(body), infer_schema_length=0)
    missing = [c for c in REQUIRED if c not in table.columns]
    if missing:
        raise ValueError(f"Ember yearly file is missing columns {missing}; the download layout changed")

    total = table.filter(pl.col("Electricity source") == "Total generation").with_columns(
        pl.col(INTENSITY).cast(pl.Float64).alias("intensity")
    )
    keep = total.filter(
        ((pl.col("Area type") == "Country or economy") | ((pl.col("Area type") == "Region") & (pl.col("Area") == "World")))
        & pl.col("intensity").is_not_null()
        & (pl.col("intensity") >= 0)
        & ~pl.col("ISO 3 code").fill_null("").is_in(list(NOT_IN_REGISTRY))
    )
    if keep.is_empty():
        raise ValueError("no Ember 'Total generation' intensities found")
    newest = keep["Year"].max()
    release = f"yearly data to {newest}"

    regions: dict[str, str] = {}
    for area, code, area_type in keep.select("Area", "ISO 3 code", "Area type").unique().iter_rows():
        regions[area] = "world" if area_type == "Region" else country_from_iso3(code or "")

    rows = [
        {
            "source": SOURCE,
            "source_version": release,
            "edition": year,
            "source_ref": None,
            "scope": "2",
            "category": "electricity",
            "activity": ACTIVITY,
            "region": regions[area],
            "region_type": "world" if regions[area] == "world" else "country",
            "activity_unit": "kWh",
            "gas": "CO2e",
            "value": round(intensity / 1000, 9),  # g -> kg; rounding drops binary noise from the division
            "gwp_set": "mixed",
            "valid_year": year,
            "notes": NOTE,
        }
        for area, year, intensity in keep.select("Area", "Year", "intensity").iter_rows()
    ]
    return rows_frame(rows)
