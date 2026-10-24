"""State and national climate from NOAA NCEI nClimDiv's statewide files: monthly average, maximum and
minimum temperature and precipitation for each state and the contiguous US, each with its 1991-2020
normal, anomaly and rank. Regional and special-area series (codes 101-109, 111+) are not published.
"""

from __future__ import annotations

import polars as pl

from . import nclimdiv


def resources(ctx) -> dict[str, str]:
    return nclimdiv.file_urls(nclimdiv.procdate(ctx.get(nclimdiv.PROCDATE_URL)), "st")


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return nclimdiv.build(raw, level="st", location=nclimdiv.state_location())
