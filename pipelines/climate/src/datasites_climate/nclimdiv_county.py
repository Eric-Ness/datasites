"""County climate report card from NOAA NCEI nClimDiv: every county's monthly average, maximum and
minimum temperature and precipitation, each with its 1991-2020 normal, anomaly and rank.

Coverage follows the files: the contiguous US from 1895, Alaska boroughs from 1925, Hawaii from 1991.
Connecticut is still published as its 8 pre-2022 counties (09001-09015), which the registry (Census 2024,
planning regions) no longer has; the contract lists them as explicit legacy locations.
"""

from __future__ import annotations

import polars as pl

from . import nclimdiv


def resources(ctx) -> dict[str, str]:
    """procdate.txt (9 bytes) names the current files; each is then fetched with a conditional GET."""
    return nclimdiv.file_urls(nclimdiv.procdate(ctx.get(nclimdiv.PROCDATE_URL)), "cy")


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return nclimdiv.build(raw, level="cy", location=nclimdiv.county_location())
