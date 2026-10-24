"""waterdata/stream-gages: every USGS stream gage with daily mean discharge in the last 400 days.

Sources (USGS Water Data OGC APIs, CSV):
  time-series-metadata  daily mean discharge series (00060/00003) whose latest value is recent
  monitoring-locations  name, site type, state, county, HUC, coordinates and drainage area, 400 ids per request
Selection and series choice are described in datasites_water.usgs.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from . import usgs


def collect(ctx: Any) -> dict[str, bytes]:
    start, end = usgs.window(ctx.now)
    return {"query": usgs.query_entry(start, end), **usgs.collect_gages(ctx, start)}


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return usgs.gages(raw)
