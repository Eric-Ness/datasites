"""healthdata/overdose-deaths-county: VSRR provisional drug overdose deaths by county (see vsrr_overdose)."""

from __future__ import annotations

import polars as pl

from .vsrr_overdose import COUNTY_ID, csv_url, transform_counties

RESOURCES = {"counties": csv_url(COUNTY_ID)}


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return transform_counties(raw["counties"])
