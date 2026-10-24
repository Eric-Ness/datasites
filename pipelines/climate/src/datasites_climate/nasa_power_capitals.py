"""Monthly temperature at each country's capital from NASA POWER (MERRA-2, 1981 on), with a 1991-2020
normal, anomaly and rank computed here the same way as the county report card.

Precipitation (PRECTOTCORR) is deliberately not published. In the October 2026 data some cells have
months 10-30 times their normal that no documented unit or product change explains: Singapore reads
89-148 mm/day in every month of 2026 (normal ~5), and Muscat 79 mm/day in August 2024 (normal 0.3).
Across all capitals the median value/normal ratio stays near 1 in every year, so it is not a units
switch that could be converted; the artifacts are isolated and cannot be told apart from real
extremes reliably. See the contract.

Capitals: Natural Earth populated places at a pinned tag, features with FEATURECLA "Admin-0 capital".
Four countries have more than one such feature; we keep the one Natural Earth marks as the capital
(ADM0CAP 1, CAPALT 0) and, among those, the most populous:
    South Africa  Pretoria (not Bloemfontein, Cape Town or Johannesburg)
    Bolivia       La Paz (not Sucre)
    Cote d'Ivoire Abidjan (Yamoussoukro is flagged CAPALT, an alternate capital)
    Myanmar       Naypyidaw (not Yangon)
Kosovo (KOS) and Somaliland (SOL) are not ISO 3166 countries and have no registry id, so they are skipped.

NASA POWER publishes no numeric rate limit and answers 429 when pushed, so requests go one at a time
with a pause between them; the dataset is checked weekly.
"""

from __future__ import annotations

import json
import time

import httpx
import polars as pl

NATURAL_EARTH_TAG = "v5.1.2"
PLACES_URL = (
    f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NATURAL_EARTH_TAG}/geojson/"
    "ne_110m_populated_places.geojson"
)
POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
PARAMETERS = {"tavg": "T2M"}
UNITS = {"T2M": "C"}
FIRST_YEAR = 1981  # MERRA-2 meteorology starts here
REQUEST_DELAY_S = 1.0
NORMAL_FIRST, NORMAL_LAST = 1991, 2020
MIN_NORMAL_YEARS = 24  # WMO practice: a normal needs at least 80% of its 30 years


def _registry_countries() -> dict[str, str]:
    """Registry country id -> ISO alpha-2."""
    from datasites_core import geo

    return {loc_id: loc["iso2"] for loc_id, loc in geo.load().items() if loc["level"] == "country"}


def capitals(places: bytes) -> pl.DataFrame:
    """location (registry country id), capital, latitude, longitude: one row per country, sorted by id."""
    countries = _registry_countries()
    rows = []
    for feature in json.loads(places)["features"]:  # ~250 places
        p = feature["properties"]
        if p.get("FEATURECLA") != "Admin-0 capital":
            continue
        location = str(p["ADM0_A3"]).lower()
        if location not in countries:
            continue  # KOS, SOL: no ISO 3166 code
        if p.get("ISO_A2") not in ("-99", None) and p["ISO_A2"] != countries[location]:
            raise ValueError(f"{p['NAME']}: ADM0_A3 {p['ADM0_A3']} disagrees with ISO_A2 {p['ISO_A2']}")
        lon, lat = feature["geometry"]["coordinates"][:2]
        rows.append(
            {
                "location": location,
                "capital": " ".join(str(p["NAME"]).split()),  # NE writes "Washington,  D.C."
                "latitude": round(float(lat), 4),
                "longitude": round(float(lon), 4),
                "preferred": p.get("ADM0CAP") == 1 and not p.get("CAPALT"),
                "population": p.get("POP_MAX") or 0,
            }
        )
    if not rows:
        raise ValueError("no Admin-0 capitals in the Natural Earth file")
    return (
        pl.DataFrame(rows)
        .sort(["location", "preferred", "population", "capital"], descending=[False, True, True, False])
        .unique("location", keep="first", maintain_order=True)
        .select("location", "capital", "latitude", "longitude")
    )


def _request(ctx, row: dict, end: int) -> bytes:
    params = {
        "parameters": ",".join(PARAMETERS.values()),
        "community": "AG",
        "longitude": f"{row['longitude']:.4f}",
        "latitude": f"{row['latitude']:.4f}",
        "start": FIRST_YEAR,
        "end": end,
        "format": "JSON",
    }
    return ctx.get(POWER_URL, params=params)


def collect(ctx) -> dict[str, bytes]:
    places = ctx.get(PLACES_URL)
    out = {"places": places}
    end = ctx.now.year
    for i, row in enumerate(capitals(places).iter_rows(named=True)):  # one request per capital, not per row
        if i:
            time.sleep(REQUEST_DELAY_S)
        try:
            out[f"power-{row['location']}"] = _request(ctx, row, end)
        except httpx.HTTPStatusError as err:
            # Early in a year POWER may not accept the new year as an end date yet (422 "end date out of range").
            if i == 0 and err.response.status_code == 422 and end == ctx.now.year:
                end -= 1
                time.sleep(REQUEST_DELAY_S)
                out[f"power-{row['location']}"] = _request(ctx, row, end)
            else:
                raise
    return out


def normalize(name: str, body: bytes) -> bytes:
    """POWER responses carry request timings ("times"), which differ on every call."""
    if not name.startswith("power-"):
        return body
    doc = json.loads(body)
    doc.pop("times", None)
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _point(body: bytes, row: dict) -> pl.DataFrame:
    doc = json.loads(body)
    header, units = doc.get("header", {}), doc.get("parameters", {})
    if "properties" not in doc:
        raise ValueError(f"{row['location']}: POWER returned no data: {doc.get('messages')}")
    lon, lat = doc["geometry"]["coordinates"][:2]
    if abs(lon - row["longitude"]) > 0.01 or abs(lat - row["latitude"]) > 0.01:
        raise ValueError(f"{row['location']}: response is for ({lat}, {lon}), not {row['capital']}")
    fill = header.get("fill_value", -999.0)
    frames = []
    for series, param in PARAMETERS.items():
        if units.get(param, {}).get("units") != UNITS[param]:
            raise ValueError(f"{row['location']}: {param} units are {units.get(param)}, expected {UNITS[param]}")
        values = doc["properties"]["parameter"][param]  # {"198101": 2.42, ..., "198113": annual}
        frames.append(
            pl.DataFrame(
                {"key": list(values.keys()), "raw": list(values.values())},
                schema={"key": pl.Utf8, "raw": pl.Float64},
            ).with_columns(pl.lit(series).alias("series"), pl.lit(fill, dtype=pl.Float64).alias("fill"))
        )
    return pl.concat(frames).with_columns(
        pl.lit(row["location"]).alias("location"),
        pl.lit(row["capital"]).alias("capital"),
        pl.lit(row["latitude"]).alias("latitude"),
        pl.lit(row["longitude"]).alias("longitude"),
    )


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    caps = capitals(raw["places"])
    missing = [loc for loc in caps["location"] if f"power-{loc}" not in raw]
    if missing:
        raise ValueError(f"no POWER response for {missing[:10]}")
    points = pl.concat([_point(raw[f"power-{row['location']}"], row) for row in caps.iter_rows(named=True)])

    year = pl.col("key").str.slice(0, 4).cast(pl.Int32, strict=True)
    month = pl.col("key").str.slice(4, 2).cast(pl.Int8, strict=True)
    table = (
        points.filter(pl.col("key").str.slice(4, 2) != "13")  # annual means
        .with_columns(
            year.alias("year"),
            month.alias("month"),
            pl.when(pl.col("raw") == pl.col("fill")).then(None).otherwise(pl.col("raw")).alias("value"),
        )
        .with_columns(pl.format("{}-{}", "year", pl.col("month").cast(pl.Utf8).str.zfill(2)).alias("date"))
    )
    bad = table.filter((pl.col("month") < 1) | (pl.col("month") > 12))
    if len(bad):
        raise ValueError(f"unexpected POWER time key {bad['key'][0]!r}")

    place = ["series", "location"]
    # Months after the last reported one are fill values for the future, not gaps.
    table = table.filter(pl.col("date") <= pl.col("date").filter(pl.col("value").is_not_null()).max().over(place))

    in_normal = pl.col("year").is_between(NORMAL_FIRST, NORMAL_LAST)
    normal_values = pl.col("value").filter(in_normal)
    normal = (
        pl.when(normal_values.count().over([*place, "month"]) >= MIN_NORMAL_YEARS)
        .then(normal_values.mean().over([*place, "month"]))
        .otherwise(None)
        .round(2)
    )
    return (
        table.with_columns(normal.alias("normal"))
        .select(
            *place,
            "date",
            "value",
            "normal",
            (pl.col("value") - pl.col("normal")).round(2).alias("anomaly"),
            pl.col("value").rank(method="min", descending=True).over([*place, "month"]).cast(pl.Int32).alias("rank"),
            "capital",
            "latitude",
            "longitude",
        )
    )
