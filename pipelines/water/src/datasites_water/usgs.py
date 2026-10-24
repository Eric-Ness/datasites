"""Shared access to the USGS Water Data OGC APIs (https://api.waterdata.usgs.gov/ogcapi/v1/).

Built only on the modernized APIs: the legacy waterservices.usgs.gov service shuts down on 2027-02-22.

Every request asks for CSV (`f=csv`, only the properties we use, coordinates only where needed), which is
several times smaller than GeoJSON. CSV responses carry no "next" link, so queries are sized to return less
than one page (50,000 rows); a response that fills the page is split and asked again (usgs_daily.fetch_block).

An api.data.gov key raises the rate limit. It is optional: set USGS_WATERDATA_API_KEY to send it in the
X-Api-Key header (never in the URL, so it can't leak into logs). Without it the lower anonymous limit
applies (the API reports X-RateLimit-Limit: 1000 an hour, counted per IP address, so other anonymous clients
on a shared CI runner address count too); a daily run of both USGS datasets makes about 130 requests.

Stream gages are selected the same way for stream-gages and streamflow-daily:
  - USGS-operated monitoring locations (agency USGS; ids usgs-<8 to 15 digits>) whose site type is a stream
    (ST, ST-CA canal, ST-DCH ditch, ST-TS tidal stream);
  - with a daily mean discharge time series (parameter 00060, statistic 00003, ft^3/s) whose latest value
    falls inside the rolling window (the last WINDOW_DAYS days).
A few gages have more than one such series (a second record adjusted for diversions, or inflow and outflow
sublocations). One is chosen per gage: the series flagged Primary, then the one without a sublocation, then
the one without a special web description, then the most recent, then the longest record.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import polars as pl

from datasites_core import geo
from datasites_core.fetch import request

API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections"
KEY_ENV = "USGS_WATERDATA_API_KEY"
PAGE_LIMIT = 50_000  # the API's maximum page size
ID_BATCH_CHARS = 5600  # monitoring-location ids per request, by URL length (about 400 ids; ~9.8 KB is rejected with 414)
WINDOW_DAYS = 400
ATTEMPTS = 8  # per request, for 429 and 5xx answers and dropped connections
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_WAIT = 20 * 60  # seconds; a longer Retry-After is capped (the next answer brings a fresh one)
DISCHARGE = "00060"
DAILY_MEAN = "00003"
UNIT = "ft^3/s"
STREAM_SITE_TYPES = ("ST", "ST-CA", "ST-DCH", "ST-TS")
SITE_PATTERN = r"^USGS-\d{8,15}$"

SERIES_PROPERTIES = "id,monitoring_location_id,unit_of_measure,begin,end,primary,sublocation_identifier,web_description"
LOCATION_PROPERTIES = (
    "id,monitoring_location_name,agency_code,country_code,state_code,county_code,county_name,site_type_code,site_type,"
    "hydrologic_unit_code,drainage_area,contributing_drainage_area,altitude,vertical_datum,time_zone_abbreviation"
)
SERIES_COLUMNS = ["x", "y", *SERIES_PROPERTIES.split(",")]
LOCATION_COLUMNS = ["x", "y", *LOCATION_PROPERTIES.split(",")]


# --- requests -------------------------------------------------------------------------------------


def headers() -> dict[str, str]:
    key = os.environ.get(KEY_ENV)
    return {"X-Api-Key": key} if key else {}


def get_csv(ctx: Any, collection: str, params: dict[str, Any], *, geometry: bool = False) -> bytes:
    """One CSV page. Coordinates (the x and y columns) are only filled in when geometry is asked for."""
    query = {"f": "csv", "limit": PAGE_LIMIT, "skipGeometry": "false" if geometry else "true", **params}
    return get(ctx, f"{API}/{collection}/items", query)


def get(ctx: Any, url: str, params: dict[str, Any]) -> bytes:
    """GET with the engine's User-Agent, one request at a time.

    This keeps its own retry loop, with more attempts than ctx.get, so a throttled run rides out a long
    stretch of 429s and every attempt counts toward ctx.requests. Like the engine, a 429 waits exactly as long
    as Retry-After asks (no less, so it stays polite), and 5xx answers and dropped connections back off from
    5 seconds.
    """
    backoff = 5.0
    for attempt in range(ATTEMPTS):
        ctx.requests = getattr(ctx, "requests", 0) + 1
        try:
            return request(url, params=params, headers=headers(), retries=0).content
        except httpx.HTTPStatusError as err:
            if err.response.status_code not in RETRY_STATUSES or attempt == ATTEMPTS - 1:
                raise
            asked = err.response.headers.get("retry-after", "")
            wait = float(asked) if asked.isdigit() else backoff
            reason = f"HTTP {err.response.status_code}"
        except httpx.TransportError as err:
            if attempt == ATTEMPTS - 1:
                raise
            wait, reason = backoff, type(err).__name__
        wait = min(wait, MAX_WAIT)
        print(f"USGS {url.rsplit('/', 2)[-2]}: {reason}, retrying in {wait:.0f}s", file=sys.stderr, flush=True)
        time.sleep(wait)
        backoff = min(backoff * 3, 300)
    raise AssertionError("unreachable")


def window(now: datetime) -> tuple[date, date]:
    """The rolling window: the last WINDOW_DAYS days up to today (UTC), inclusive."""
    end = now.date()
    return end - timedelta(days=WINDOW_DAYS - 1), end


def query_entry(start: date, end: date, **extra: Any) -> bytes:
    """A small raw entry recording the window, so transforms stay pure (they never read the clock)."""
    return json.dumps({"window_start": start.isoformat(), "window_end": end.isoformat(), **extra}, sort_keys=True).encode()


def read_query(raw: dict[str, bytes]) -> dict[str, Any]:
    return json.loads(raw["query"])


def collect_gages(ctx: Any, start: date) -> dict[str, bytes]:
    """Daily discharge time series ending inside the window, and the monitoring locations they belong to."""
    # `end` is a UTC timestamp of local midnight; two days of slack covers every US time zone (Guam is UTC+10).
    # The exact cut is made in transform, on the local date.
    cutoff = (start - timedelta(days=2)).isoformat()
    series = get_csv(
        ctx,
        "time-series-metadata",
        {
            "parameter_code": DISCHARGE,
            "statistic_id": DAILY_MEAN,
            "properties": SERIES_PROPERTIES,
            "filter": f"end >= '{cutoff}'",
        },
    )
    table = read_csv(series, SERIES_COLUMNS)
    if len(table) >= PAGE_LIMIT:
        raise RuntimeError(f"time-series-metadata returned a full page ({len(table)} rows); results may be truncated")
    ids = sorted(set(table.filter(pl.col("monitoring_location_id").str.contains(SITE_PATTERN))["monitoring_location_id"]))
    if not ids:
        raise RuntimeError("time-series-metadata returned no USGS daily discharge series")
    raw = {"time-series": series}
    for n, batch in enumerate(id_batches(ids)):
        raw[f"locations-{n:03d}"] = get_csv(
            ctx, "monitoring-locations", {"id": ",".join(batch), "properties": LOCATION_PROPERTIES}, geometry=True
        )
    return raw


def id_batches(ids: list[str]) -> list[list[str]]:
    """Consecutive runs of ids whose comma-joined length stays within ID_BATCH_CHARS."""
    batches: list[list[str]] = []
    length = 0
    for site in ids:
        if batches and length + 1 + len(site) <= ID_BATCH_CHARS:
            batches[-1].append(site)
            length += 1 + len(site)
        else:
            batches.append([site])
            length = len(site)
    return batches


# --- parsing --------------------------------------------------------------------------------------


def read_csv(body: bytes, columns: list[str]) -> pl.DataFrame:
    """A CSV response as all-string columns. The API answers an empty result with an empty body."""
    if not body.strip():
        return pl.DataFrame({c: [] for c in columns}, schema={c: pl.Utf8 for c in columns})
    table = pl.read_csv(io.BytesIO(body), infer_schema=False)
    missing = [c for c in columns if c not in table.columns]
    if missing:
        raise ValueError(f"USGS response is missing columns {missing} (got {table.columns})")
    return table.select(columns)


def local_date(column: str) -> pl.Expr:
    """The local calendar date of a time-series `begin`/`end`.

    USGS reports these as UTC timestamps of local midnight ('2026-10-22 04:00:00+00:00' is midnight EDT).
    Shifting by 12 hours and keeping the date recovers the local date for every UTC offset from -12 to +11
    hours, which covers every US state and territory, without needing a time zone database.
    """
    text = pl.col(column)
    parsed = text.str.slice(0, 19).str.to_datetime("%Y-%m-%d %H:%M:%S", strict=True)
    return (parsed + pl.duration(hours=12)).dt.date().cast(pl.Utf8)


def _registry_maps() -> tuple[dict[str, str], dict[str, str]]:
    places = geo.load().values()
    states = {p["fips"]: p["usps"] for p in places if p["level"] == "us-state"}
    countries = {p["iso2"]: p["id"] for p in places if p["level"] == "country"}
    return states, countries


def _float(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Float64, strict=True)


def gages(raw: dict[str, bytes]) -> pl.DataFrame:
    """One row per USGS stream gage with a daily mean discharge series in the window (see the module doc)."""
    start = read_query(raw)["window_start"]
    series = read_csv(raw["time-series"], SERIES_COLUMNS)
    units = set(series["unit_of_measure"].drop_nulls())
    if units - {UNIT}:
        raise ValueError(f"unexpected discharge units {sorted(units - {UNIT})}; expected {UNIT}")
    for col in ("begin", "end"):
        bad = series.filter(pl.col(col).is_not_null() & ~pl.col(col).str.ends_with("+00:00"))
        if len(bad):
            raise ValueError(f"time-series {col} is not a UTC timestamp: {bad[col][0]!r}")

    series = (
        series.filter(pl.col("monitoring_location_id").str.contains(SITE_PATTERN) & pl.col("end").is_not_null())
        .with_columns(local_date("begin").alias("daily_begin"), local_date("end").alias("daily_end"))
        .filter(pl.col("daily_end") >= start)
        .sort(
            [
                "monitoring_location_id",
                pl.col("primary").fill_null("").ne("Primary"),
                pl.col("sublocation_identifier").is_not_null(),
                pl.col("web_description").is_not_null(),
                "daily_end",
                "daily_begin",
                "id",
            ],
            descending=[False, False, False, False, True, False, False],
            nulls_last=True,
        )
        .unique(subset="monitoring_location_id", keep="first", maintain_order=True)
        .select("id", "monitoring_location_id", "sublocation_identifier", "daily_begin", "daily_end")
    )

    locations = pl.concat([read_csv(raw[name], LOCATION_COLUMNS) for name in sorted(raw) if name.startswith("locations-")])
    locations = locations.filter(
        pl.col("agency_code").eq("USGS") & pl.col("site_type_code").is_in(STREAM_SITE_TYPES)
    ).unique(subset="id", keep="first", maintain_order=True)

    joined = series.join(locations, left_on="monitoring_location_id", right_on="id", how="inner")
    states, countries = _registry_maps()
    in_us = pl.col("country_code").eq("US")
    usps = pl.col("state_code").replace_strict(states, default=None)
    county_known = in_us & pl.col("county_code").str.contains(r"^\d{3}$") & pl.col("county_code").ne("000")
    huc = pl.col("hydrologic_unit_code")
    number = pl.col("monitoring_location_id").str.slice(5)
    return joined.select(
        ("usgs-" + number).alias("site_id"),
        number.alias("site_number"),
        pl.col("monitoring_location_name").alias("name"),
        "site_type_code",
        "site_type",
        pl.when(in_us & usps.is_not_null())
        .then("us-" + usps.str.to_lowercase())
        .otherwise(pl.col("country_code").replace_strict(countries, default="usa"))
        .alias("region"),
        pl.col("country_code").replace_strict(countries, default=None).alias("country"),
        pl.when(in_us).then(usps).alias("state"),
        pl.when(county_known).then(pl.col("state_code") + pl.col("county_code")).alias("county_fips"),
        pl.when(county_known).then(pl.col("county_name")).alias("county_name"),
        huc.alias("huc"),
        pl.when(huc.str.len_chars() >= 8).then(huc.str.slice(0, 8)).alias("huc8"),
        _float("y").alias("latitude"),
        _float("x").alias("longitude"),
        _float("drainage_area").alias("drainage_area"),
        _float("contributing_drainage_area").alias("contributing_drainage_area"),
        _float("altitude").alias("altitude"),
        pl.col("vertical_datum").alias("altitude_datum"),
        pl.col("time_zone_abbreviation").alias("time_zone"),
        pl.col("id").alias("time_series_id"),
        pl.col("sublocation_identifier").alias("sublocation"),
        "daily_begin",
        "daily_end",
    )
