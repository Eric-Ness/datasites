"""healthdata/overdose-deaths: VSRR provisional drug overdose deaths by state and drug (see vsrr_overdose)."""

from __future__ import annotations

import polars as pl

from .vsrr_overdose import STATE_ID, csv_url, transform_states

RESOURCES = {"states": csv_url(STATE_ID)}


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return transform_states(raw["states"])
