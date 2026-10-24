"""Preliminary daily AQI by county from EPA AirNow's public daily files (files.airnowtech.org, no key).

Files (see the AirNow "Daily Data File" fact sheet, https://docs.airnowapi.org/docs/DailyDataFactSheet.pdf):
  airnow/YYYY/YYYYMMDD/daily_data_v2.dat
      pipe-delimited, no header. One row per site and parameter for one day:
      valid date (mm/dd/yy) | AQSID | site name | parameter | units | value | averaging hours | data source |
      AQI (-999 when the parameter has no daily AQI: 1-hour ozone, 24-hour SO2) | AQI category code |
      latitude | longitude | full AQSID (3-digit country code + AQSID)
      "Valid date" is the local date: daily values are midnight to midnight in local standard time, the
      same day boundary AirData uses, so no time-zone shift is applied. Each day's file is rewritten
      twice an hour until about 23:50 UTC the next day.
  airnow/YYYY/YYYYMMDD/Monitoring_Site_Locations_V2.dat
      pipe-delimited with a header; one row per site and parameter, with StateAQSCode and CountyAQSCode.

How a county value is built (EPA's convention, the one AirData uses: the highest daily AQI among the
county's monitors represents the county):
  - US sites only: the full AQSID starts with a US country code (840; 630 Puerto Rico; 850 US Virgin
    Islands; 316 Guam).
  - A nine-digit numeric AQSID is an AQS site id, state (2) + county (3) + site (4), so the county is its
    first five digits. That is how AQS, and so AirData, files the site. The site-locations file is not
    used for these ids: it has stale county codes for some sites (e.g. 530370002 Ellensburg, Kittitas
    County, is listed under Chelan).
  - Tribal sites ("TT" ids) carry no county in their id; their county comes from the site-locations file,
    and only when it gives a single US county for the id. A tribal monitor that AirNow also reports under
    its AQS id (same coordinates, same values) is kept once, under the AQS id.
  - Every other id (temporary and mobile monitors such as "840MM..." smoke monitors) is left out: those
    monitors are not in AQS, so AirData's county AQI never includes them.
  - Site AQI values are used exactly as received; only rows with an AQI (>= 0) count. The county AQI is
    the highest of them, the defining parameter and site are that row's, and sites_reporting counts the
    distinct sites with any AQI that day. The category follows from the AQI.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

import polars as pl

from .aqi import category_expr

FILES = "https://files.airnowtech.org/airnow"
LISTING = "https://s3-us-west-1.amazonaws.com/files.airnowtech.org/"  # the bucket behind files.airnowtech.org
RECHECK_DAYS = (
    3  # newest days whose folder listing is checked for the daily file before fetching
)
WINDOW_DAYS = 366  # EPA's AirData lags at most ~7 months before a release fills the gap; a year leaves margin
US_COUNTRY_CODES = ("840", "630", "850", "316")
DAILY_COLUMNS = [
    "valid_date", "aqsid", "site_name", "parameter", "units", "value", "averaging_hours",
    "data_source", "aqi", "category_code", "latitude", "longitude", "full_aqsid",
]  # fmt: skip
# AirNow parameter name -> AirData's "Defining Parameter" spelling. Only these carry a daily AQI.
PARAMETERS = {
    "OZONE-8HR": "Ozone",
    "PM2.5-24hr": "PM2.5",
    "PM10-24hr": "PM10",
    "CO-8hr": "CO",
    "NO2-1HR": "NO2",
    "SO2-1HR": "SO2",
}
# Ties on the AQI go to the first of these, then to the lowest site id, so the choice is deterministic.
PARAMETER_ORDER = ["Ozone", "PM2.5", "PM10", "CO", "NO2", "SO2"]
SITES_FILE = "Monitoring_Site_Locations_V2.dat"
UNIT_SEPARATOR = chr(0x1F)  # a byte the files never contain: reads each line whole
CR = chr(13)
LF = bytes([10])
DAY_NAME = re.compile(r"^airnow-(\d{8})$")


def window(now: datetime) -> tuple[date, date]:
    """First and last day to fetch: a year back from yesterday (today's file is still filling up)."""
    last = now.date() - timedelta(days=1)
    return last - timedelta(days=WINDOW_DAYS - 1), last


def listed_days(ctx, years: range) -> set[str]:
    """Day folders (YYYYMMDD) that exist for these years, from the bucket listing (one request a year)."""
    days: set[str] = set()
    for year in years:
        body = ctx.get(
            LISTING, params={"prefix": f"airnow/{year}/", "delimiter": "/"}
        ).decode("utf-8")
        if "<IsTruncated>true" in body:
            raise ValueError(f"the AirNow listing for {year} is truncated")
        days |= set(
            re.findall(rf"<Prefix>airnow/{year}/({year}\d{{4}})/</Prefix>", body)
        )
    return days


def folder_files(ctx, day: str) -> set[str]:
    """File names in one day folder (about 150 keys, one request)."""
    body = ctx.get(LISTING, params={"prefix": f"airnow/{day[:4]}/{day}/"}).decode(
        "utf-8"
    )
    return set(re.findall(rf"<Key>airnow/{day[:4]}/{day}/([^<]+)</Key>", body))


def resources(ctx, now: datetime) -> dict[str, str]:
    """One daily file per day in the window, plus one site-locations file per month.

    A missing file would fail the whole run (and, inside the window, keep failing for a year), so the
    newest days, whose files are still being written, and the locations file are checked in their folder
    listings first. Older days were checked the same way when they were new.
    """
    first, last = window(now)
    days = sorted(listed_days(ctx, range(first.year, last.year + 1)))
    wanted = [
        d for d in days if first.strftime("%Y%m%d") <= d <= last.strftime("%Y%m%d")
    ]
    if not wanted:
        raise ValueError(f"no AirNow day folders between {first} and {last}")
    recent = [
        d
        for d in wanted
        if d > (last - timedelta(days=RECHECK_DAYS)).strftime("%Y%m%d")
    ]
    missing = {d for d in recent if "daily_data_v2.dat" not in folder_files(ctx, d)}
    res = {
        f"airnow-{d}": f"{FILES}/{d[:4]}/{d}/daily_data_v2.dat"
        for d in wanted
        if d not in missing
    }

    # The locations file is regenerated (4 MB) every day; one a month is enough to place the few tribal
    # sites, and keeps the raw archive from growing by a copy a day. Prefer the month's first folder.
    anchor = (now.date() - timedelta(days=2)).replace(day=1).strftime("%Y%m%d")
    candidates = [anchor] if anchor in wanted else []
    candidates += [d for d in reversed(wanted) if d < anchor][:5] + [
        d for d in wanted if d > anchor
    ][:5]
    for day in candidates:
        if SITES_FILE in folder_files(ctx, day):
            res["airnow-sites"] = f"{FILES}/{day[:4]}/{day}/{SITES_FILE}"
            break
    else:
        raise ValueError(f"no {SITES_FILE} near {anchor}")
    return res


def _split(body: bytes, fields: int, where: str) -> pl.DataFrame:
    """Pipe-delimited lines as a frame of `fields` text columns (f0, f1, ...). Lines with another number of
    fields (a stray '|' in a site name) are dropped rather than shifting every later column."""
    lines = pl.read_csv(
        io.BytesIO(body), separator=UNIT_SEPARATOR, has_header=False, new_columns=["line"],
        infer_schema=False, quote_char=None, encoding="utf8-lossy",
    )  # fmt: skip
    parts = lines.select(
        pl.col("line").str.strip_chars_end(CR).str.split("|").alias("f")
    )
    good = parts.filter(pl.col("f").list.len() == fields)
    bad = len(parts) - len(good)
    if bad > max(
        10, 0.01 * len(parts)
    ):  # more than a stray line or two: the format changed
        raise ValueError(
            f"{where}: {bad} of {len(parts)} lines do not have {fields} fields"
        )
    return good.select([pl.col("f").list.get(i).alias(f"f{i}") for i in range(fields)])


def read_daily(body: bytes, day: str) -> pl.DataFrame:
    """One daily_data_v2.dat, keeping US rows with an AQI for that day."""
    df = _split(body, len(DAILY_COLUMNS), f"AirNow daily file {day}")
    df = df.rename({f"f{i}": name for i, name in enumerate(DAILY_COLUMNS)})
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    df = df.with_columns(
        # mm/dd/yy; AirNow starts in the 2000s, so yy is 20yy
        date=pl.concat_str(pl.lit("20"), pl.col("valid_date").str.slice(6, 2), pl.lit("-"),
                           pl.col("valid_date").str.slice(0, 2), pl.lit("-"), pl.col("valid_date").str.slice(3, 2)),
        # AQIs are whole numbers; anything else is not a usable AQI and drops out with the -999s below
        aqi=pl.col("aqi").str.strip_chars().cast(pl.Int64, strict=False),
        country=pl.col("full_aqsid").str.strip_chars().str.slice(0, 3),
        parameter=pl.col("parameter").str.strip_chars(),
        aqsid=pl.col("aqsid").str.strip_chars(),
    )  # fmt: skip
    # A day's folder holds that day's file; anything else in it belongs to another folder.
    keep = (
        (pl.col("date") == iso)
        & (pl.col("aqi") >= 0)
        & pl.col("country").is_in(US_COUNTRY_CODES)
    )
    return df.filter(keep.fill_null(False)).select(
        "date",
        "aqsid",
        "parameter",
        "aqi",
        "country",
        pl.col("latitude").str.strip_chars(),
        pl.col("longitude").str.strip_chars(),
    )


def tribal_counties(body: bytes) -> pl.DataFrame:
    """AQSID -> county FIPS for tribal ("TT") sites, from the site-locations file; ambiguous ids are dropped."""
    header = body.split(LF, 1)[0].decode("utf-8", "replace").strip().split("|")
    needed = ["AQSID", "CountryFIPS", "StateAQSCode", "CountyAQSCode"]
    missing = [c for c in needed if c not in header]
    if missing:
        raise ValueError(
            f"{SITES_FILE}: columns {missing} not found; the header is {header}"
        )
    df = _split(body, len(header), SITES_FILE).slice(1)
    col = {name: pl.col(f"f{header.index(name)}").str.strip_chars() for name in needed}
    df = df.select(
        aqsid=col["AQSID"],
        country=col["CountryFIPS"],
        state=col["StateAQSCode"],
        county=col["CountyAQSCode"],
    )
    df = df.filter(
        pl.col("aqsid").str.starts_with("TT")
        & (pl.col("country") == "US")
        & pl.col("state").str.contains(r"^\d{2}$")
        & pl.col("county").str.contains(r"^\d{3}$")
    ).select("aqsid", fips=pl.col("state") + pl.col("county"))
    grouped = df.unique().group_by("aqsid").agg(pl.col("fips").unique())
    return grouped.filter(pl.col("fips").list.len() == 1).select(
        "aqsid", pl.col("fips").list.first()
    )


def site_label(aqsid: pl.Expr) -> pl.Expr:
    """AQS ids in AirData's 'SS-CCC-NNNN' form; other AirNow ids as received."""
    numeric = aqsid.str.contains(r"^\d{9}$")
    dashed = pl.concat_str(
        aqsid.str.slice(0, 2),
        pl.lit("-"),
        aqsid.str.slice(2, 3),
        pl.lit("-"),
        aqsid.str.slice(5, 4),
    )
    return pl.when(numeric).then(dashed).otherwise(aqsid)


def county_days(raw: dict[str, bytes]) -> pl.DataFrame:
    """Preliminary county AQI per day: columns fips, date, aqi, category, defining_parameter, defining_site,
    sites_reporting."""
    frames = [
        read_daily(body, m.group(1))
        for name, body in sorted(raw.items())
        if (m := DAY_NAME.match(name))
    ]
    schema = {"fips": pl.Utf8, "date": pl.Utf8, "aqi": pl.Int64, "category": pl.Utf8, "defining_parameter": pl.Utf8,
              "defining_site": pl.Utf8, "sites_reporting": pl.Int64}  # fmt: skip
    if not frames:
        return pl.DataFrame(schema=schema)
    sites = pl.concat(frames)
    sites = sites.with_columns(
        defining_parameter=pl.col("parameter").replace_strict(PARAMETERS, default=None)
    )
    sites = sites.filter(pl.col("defining_parameter").is_not_null())

    numeric = sites.filter(pl.col("aqsid").str.contains(r"^\d{9}$")).with_columns(
        fips=pl.col("aqsid").str.slice(0, 5)
    )
    tribal = sites.filter(pl.col("aqsid").str.starts_with("TT"))
    if "airnow-sites" not in raw:
        raise ValueError(f"the AirNow {SITES_FILE} file is missing from the raw inputs")
    tribal = tribal.join(tribal_counties(raw["airnow-sites"]), on="aqsid", how="inner")
    # Some tribal monitors are also reported under their AQS id (TT0300001 and 250070001 are the same
    # Martha's Vineyard monitor, at the same coordinates, with the same values): keep the AQS copy only,
    # so the site is not counted twice.
    tribal = tribal.join(
        numeric.select("date", "latitude", "longitude").unique(),
        on=["date", "latitude", "longitude"],
        how="anti",
    )
    sites = pl.concat([numeric, tribal.select(numeric.columns)])
    # Puerto Rico, the Virgin Islands and Guam report under their own country codes; the county's state
    # code must then be that territory's, or the row is not what it claims to be.
    state_for_country = {"630": "72", "850": "78", "316": "66"}
    sites = sites.filter(
        (pl.col("country") == "840")
        | (
            pl.col("fips").str.slice(0, 2)
            == pl.col("country").replace_strict(state_for_country, default="")
        )
    )

    order = pl.col("defining_parameter").replace_strict(
        {p: i for i, p in enumerate(PARAMETER_ORDER)}, return_dtype=pl.Int64
    )
    sites = sites.with_columns(order=order, site=site_label(pl.col("aqsid")))
    pick = ["aqi", "order", "site"]
    out = sites.group_by("fips", "date").agg(
        pl.col("aqi").max(),
        pl.col("defining_parameter")
        .sort_by(pick, descending=[True, False, False])
        .first(),
        pl.col("site")
        .sort_by(pick, descending=[True, False, False])
        .first()
        .alias("defining_site"),
        pl.col("aqsid").n_unique().cast(pl.Int64).alias("sites_reporting"),
    )
    return out.with_columns(category=category_expr("aqi")).select(list(schema))
