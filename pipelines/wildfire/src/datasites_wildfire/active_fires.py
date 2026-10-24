"""active-fires: VIIRS fire detections over the US and Canada, grouped into fire events and linked to
the official NIFC WFIGS incident.

Steps (all on the raw bytes of one run; no clock reads):
1. FIRMS VIIRS NRT 7-day files for S-NPP, NOAA-20 and NOAA-21 (US, Alaska/Hawaii and Canada regions);
   exact repeats from overlapping regions dropped; detections older than 7 days before the newest one
   dropped.
2. Cross-satellite duplicates marked (same 0.5 km cell within one ~75 minute overpass sequence).
3. Detections clustered into events (same or adjacent 1 km cells, within 48 h, transitively).
4. Each event's centroid placed in a state or province (Natural Earth admin-1); events outside the US
   and Canada are dropped.
5. Each event linked to a WFIGS incident: a current perimeter containing its detections, else a
   perimeter within 2 km of one, else an incident origin point within a size-dependent radius.
"""

from __future__ import annotations

import math
from datetime import timedelta
import polars as pl

from . import admin1, firms
from ._common import (
    INCIDENTS_LAYER,
    KM_PER_DEG,
    MAX_RECORD_COUNT,
    PERIMETER_GEOMETRY,
    PERIMETERS_LAYER,
    PolygonIndex,
    edges_frame,
    haversine_km,
    irwin,
    iso_ms,
    number,
    page_urls,
    read_pages,
    rings_of,
)
from .wfigs_incidents import incidents as read_incidents

INCIDENT_FIELDS = [
    "OBJECTID", "IrwinID", "IncidentName", "IncidentTypeCategory", "IncidentSize", "PercentContained",
    "FireDiscoveryDateTime", "FireOutDateTime", "ModifiedOnDateTime_dt", "UniqueFireIdentifier",
]
INCIDENT_QUERY = {"outFields": ",".join(INCIDENT_FIELDS), "outSR": 4326, "returnGeometry": "true", "f": "json"}
PERIMETER_FIELDS = [
    "OBJECTID", "poly_IRWINID", "attr_IrwinID", "attr_IncidentName", "attr_IncidentTypeCategory",
    "attr_IncidentSize", "attr_PercentContained", "attr_FireDiscoveryDateTime", "attr_FireOutDateTime", "poly_GISAcres",
]
PERIMETER_QUERY = {"outFields": ",".join(PERIMETER_FIELDS), "outSR": 4326, **PERIMETER_GEOMETRY, "f": "geojson"}
PERIMETER_PAGE = 250  # perimeters carry geometry; small pages keep each response a few MB

# A heat source seen on most days of the week in the same few kilometres, with no incident, is more likely
# a steel mill, refinery, gas flare or landfill than a wildfire (FIRMS NRT files carry no static-source flag).
STATIC_MIN_DAYS = 5
STATIC_MAX_EXTENT_KM = 4.0
NEAR_PERIMETER_KM = 2.0  # active fronts run ahead of the last mapped perimeter
ORIGIN_BASE_KM = 3.0  # VIIRS geolocation plus how precisely origins are reported
ORIGIN_MAX_KM = 25.0
ACRE_KM2 = 0.0040468564224
LINK_TIME_SLACK = timedelta(hours=24)
TILE_DEG = 0.25

SCHEMA = {
    "event_id": pl.Utf8,
    "country": pl.Utf8,
    "region": pl.Utf8,
    "region_name": pl.Utf8,
    "first_detection": pl.Utf8,
    "last_detection": pl.Utf8,
    "detections": pl.Int64,
    "raw_detections": pl.Int64,
    "low_confidence_detections": pl.Int64,
    "satellites": pl.Utf8,
    "latitude": pl.Float64,
    "longitude": pl.Float64,
    "min_latitude": pl.Float64,
    "max_latitude": pl.Float64,
    "min_longitude": pl.Float64,
    "max_longitude": pl.Float64,
    "max_frp": pl.Float64,
    "days_detected": pl.Int64,
    "likely_static_source": pl.Boolean,
    "incident_irwin_id": pl.Utf8,
    "incident_name": pl.Utf8,
    "incident_type": pl.Utf8,
    "incident_acres": pl.Float64,
    "incident_percent_contained": pl.Float64,
    "incident_discovered": pl.Utf8,
    "incident_link": pl.Utf8,
    "incident_distance_km": pl.Float64,
}
ISO = "%Y-%m-%dT%H:%M:%SZ"


def resources(ctx) -> dict[str, str]:
    urls = dict(firms.file_urls())
    urls["admin1"] = admin1.URL
    for name, url in page_urls(ctx, INCIDENTS_LAYER, INCIDENT_QUERY, MAX_RECORD_COUNT).items():
        urls[f"incidents-{name}"] = url
    for name, url in page_urls(ctx, PERIMETERS_LAYER, PERIMETER_QUERY, PERIMETER_PAGE).items():
        urls[f"perimeters-{name}"] = url
    return urls


# --- events ------------------------------------------------------------------------------------------


def events(det: pl.DataFrame) -> pl.DataFrame:
    """One row per cluster, from detections carrying `kept` and `cluster`."""
    rank = pl.col("satellite").replace_strict(firms.SATELLITE_RANK, return_dtype=pl.Int8)
    lon_shift = pl.when(pl.col("longitude") < 0).then(pl.col("longitude") + 360).otherwise(pl.col("longitude"))
    first = (
        det.with_columns(rank.alias("rank"))
        .sort(["cluster", "t", "rank", "latitude", "longitude"])
        .unique("cluster", keep="first", maintain_order=True)
        .select("cluster", pl.col("satellite").alias("first_sat"), pl.col("t").alias("first_t"),
                pl.col("latitude").alias("first_lat"), pl.col("longitude").alias("first_lon"))
    )
    agg = det.group_by("cluster").agg(
        pl.col("t").min().alias("first"),
        pl.col("t").max().alias("last"),
        pl.len().alias("raw_detections"),
        pl.col("kept").sum().cast(pl.Int64).alias("detections"),
        (pl.col("confidence") == "low").sum().cast(pl.Int64).alias("low_confidence_detections"),
        pl.col("satellite").unique().sort().alias("sats"),
        pl.col("frp").max().alias("max_frp"),
        pl.col("t").dt.date().n_unique().cast(pl.Int64).alias("days_detected"),
        pl.col("ex").filter(pl.col("kept")).sum().alias("sx"),
        pl.col("ey").filter(pl.col("kept")).sum().alias("sy"),
        pl.col("ez").filter(pl.col("kept")).sum().alias("sz"),
        pl.col("latitude").min().alias("min_latitude"),
        pl.col("latitude").max().alias("max_latitude"),
        pl.col("longitude").min().alias("lon_min"),
        pl.col("longitude").max().alias("lon_max"),
        lon_shift.min().alias("lon_min_s"),
        lon_shift.max().alias("lon_max_s"),
    )
    # Detections on both sides of the 180th meridian within one fire: the plain range would span the globe.
    crosses = ((pl.col("lon_max") - pl.col("lon_min")) > 180) & ((pl.col("lon_max_s") - pl.col("lon_min_s")) < 180)

    def wrap(e: pl.Expr) -> pl.Expr:
        return pl.when(e >= 180).then(e - 360).otherwise(e)

    return agg.join(first, on="cluster").with_columns(
        # Centroid: mean of the kept detections' Earth-centred vectors, so it is right across the dateline.
        pl.arctan2(pl.col("sz"), (pl.col("sx").pow(2) + pl.col("sy").pow(2)).sqrt()).degrees().round(5).alias("latitude"),
        pl.arctan2(pl.col("sy"), pl.col("sx")).degrees().round(5).alias("longitude"),
        # A fire straddling the dateline gets min_longitude > max_longitude (GeoJSON bbox convention).
        pl.when(crosses).then(wrap(pl.col("lon_min_s"))).otherwise(pl.col("lon_min")).alias("min_longitude"),
        pl.when(crosses).then(wrap(pl.col("lon_max_s"))).otherwise(pl.col("lon_max")).alias("max_longitude"),
        pl.col("sats").list.eval(pl.element().replace_strict(firms.SATELLITE_NAMES)).list.join(",").alias("satellites"),
    )


# --- incidents ---------------------------------------------------------------------------------------


def perimeters(raw: dict[str, bytes]) -> tuple[list[tuple[str, list]], pl.DataFrame]:
    """(polygons for the index, attributes per polygon). Polygon ids are '<irwin>|<OBJECTID>'."""
    polygons = []
    rows = []
    for feature in read_pages(raw, "perimeters-page-"):
        p = feature.get("properties") or {}
        incident = irwin(p.get("poly_IRWINID")) or irwin(p.get("attr_IrwinID"))
        rings = rings_of(feature.get("geometry"))
        if incident is None or not rings:
            continue
        poly = f"{incident}|{p.get('OBJECTID')}"
        polygons.append((poly, rings))
        rows.append(
            {
                "poly": poly,
                "irwin_id": incident,
                "name": p.get("attr_IncidentName"),
                "type": p.get("attr_IncidentTypeCategory"),
                "acres": number(p.get("attr_IncidentSize")),
                "percent_contained": number(p.get("attr_PercentContained")),
                "discovered": iso_ms(p.get("attr_FireDiscoveryDateTime")),
                "out": iso_ms(p.get("attr_FireOutDateTime")),
            }
        )
    schema = {"poly": pl.Utf8, "irwin_id": pl.Utf8, "name": pl.Utf8, "type": pl.Utf8, "acres": pl.Float64,
              "percent_contained": pl.Float64, "discovered": pl.Utf8, "out": pl.Utf8}
    return polygons, pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)


def incident_table(raw: dict[str, bytes], perimeter_attrs: pl.DataFrame) -> pl.DataFrame:
    """irwin_id, name, type, acres, percent_contained, discovered, out, latitude, longitude.
    The incident layer is authoritative; perimeters of incidents it no longer lists fill in."""
    inc = read_incidents(raw, "incidents-page-").select(
        "irwin_id", "name", "type", "acres", "percent_contained", "discovered", "out", "latitude", "longitude"
    )
    extra = (
        perimeter_attrs.join(inc, on="irwin_id", how="anti")
        .sort("irwin_id", "poly").unique("irwin_id", keep="first", maintain_order=True)
        .select("irwin_id", "name", "type", "acres", "percent_contained", "discovered", "out",
                pl.lit(None, dtype=pl.Float64).alias("latitude"), pl.lit(None, dtype=pl.Float64).alias("longitude"))
    )
    return pl.concat([inc, extra])


def _parse(col: str) -> pl.Expr:
    return pl.col(col).str.strptime(pl.Datetime("us"), ISO, strict=False)


def link(det: pl.DataFrame, ev: pl.DataFrame, raw: dict[str, bytes]) -> pl.DataFrame:
    """cluster -> incident_irwin_id, incident_link, incident_distance_km (one row per linked event)."""
    polygons, perimeter_attrs = perimeters(raw)
    table = incident_table(raw, perimeter_attrs)
    timing = table.select("irwin_id", _parse("discovered").alias("disc_t"), _parse("out").alias("out_t"))
    span = ev.select("cluster", "first", "last")

    def plausible(frame: pl.DataFrame) -> pl.DataFrame:
        """Drop incidents discovered more than a day after the event's last detection, or out more than a
        day before its first."""
        f = frame.join(span, on="cluster").join(timing, on="irwin_id", how="left")
        return f.filter(
            (pl.col("disc_t").is_null() | (pl.col("disc_t") <= pl.col("last") + LINK_TIME_SLACK))
            & (pl.col("out_t").is_null() | (pl.col("out_t") >= pl.col("first") - LINK_TIME_SLACK))
        ).drop("first", "last", "disc_t", "out_t")

    pts = det.select(pl.col("det").alias("pid"), pl.col("longitude").alias("x"), pl.col("latitude").alias("y"), "cluster")
    candidates = []
    owner = perimeter_attrs.select("poly", "irwin_id")
    if polygons:
        index = PolygonIndex(edges_frame(polygons), tile_deg=TILE_DEG, band_deg=0.02)
        inside = index.contains(pts.select("pid", "x", "y")).join(owner, on="poly").join(pts.select("pid", "cluster"), on="pid")
        inside = plausible(inside.group_by("cluster", "irwin_id").agg(pl.col("pid").n_unique().alias("hits")))
        candidates.append(inside.select("cluster", "irwin_id", pl.lit(0).alias("rank"), (-pl.col("hits").cast(pl.Float64)).alias("score"),
                                        pl.lit(0.0).alias("km"), pl.lit("perimeter").alias("how")))
        rest = pts.join(inside.select("cluster"), on="cluster", how="anti")
        near = index.near(rest.select("pid", "x", "y"), NEAR_PERIMETER_KM).join(owner, on="poly").join(pts.select("pid", "cluster"), on="pid")
        near = plausible(near.group_by("cluster", "irwin_id").agg(pl.col("km").min()))
        candidates.append(near.select("cluster", "irwin_id", pl.lit(1).alias("rank"), pl.col("km").alias("score"), "km",
                                      pl.lit("near-perimeter").alias("how")))
    linked = pl.concat(candidates).select("cluster").unique() if candidates else pl.DataFrame(schema={"cluster": pl.Int64})
    rest = pts.join(linked, on="cluster", how="anti")
    origins = table.filter(pl.col("latitude").is_not_null() & pl.col("longitude").is_not_null()).with_columns(
        (ORIGIN_BASE_KM + 1.5 * (pl.col("acres").fill_null(0).clip(0) * ACRE_KM2 / math.pi).sqrt()).clip(upper_bound=ORIGIN_MAX_KM).alias("radius")
    )
    if not rest.is_empty() and not origins.is_empty():
        pad_lat = pl.col("radius") / KM_PER_DEG
        pad_lon = pad_lat / pl.col("latitude").radians().cos().clip(lower_bound=0.05)
        tiles = origins.with_columns(
            pl.int_ranges(((pl.col("longitude") - pad_lon) / TILE_DEG).floor().cast(pl.Int64), ((pl.col("longitude") + pad_lon) / TILE_DEG).floor().cast(pl.Int64) + 1).alias("tx"),
            pl.int_ranges(((pl.col("latitude") - pad_lat) / TILE_DEG).floor().cast(pl.Int64), ((pl.col("latitude") + pad_lat) / TILE_DEG).floor().cast(pl.Int64) + 1).alias("ty"),
        ).explode("tx").explode("ty").select("irwin_id", pl.col("latitude").alias("olat"), pl.col("longitude").alias("olon"), "radius", "tx", "ty")
        located = rest.with_columns((pl.col("x") / TILE_DEG).floor().cast(pl.Int64).alias("tx"), (pl.col("y") / TILE_DEG).floor().cast(pl.Int64).alias("ty"))
        near_origin = located.join(tiles, on=["tx", "ty"]).with_columns(
            haversine_km(pl.col("y"), pl.col("x"), pl.col("olat"), pl.col("olon")).alias("km")
        ).filter(pl.col("km") <= pl.col("radius"))
        near_origin = plausible(near_origin.group_by("cluster", "irwin_id").agg(pl.col("km").min()))
        candidates.append(near_origin.select("cluster", "irwin_id", pl.lit(2).alias("rank"), pl.col("km").alias("score"), "km",
                                             pl.lit("origin").alias("how")))
    if not candidates:
        return pl.DataFrame(schema={"cluster": pl.Int64, "incident_irwin_id": pl.Utf8, "incident_link": pl.Utf8, "incident_distance_km": pl.Float64})
    acres = table.select("irwin_id", pl.col("acres").fill_null(0).alias("acres_sort"))
    best = (
        pl.concat(candidates, how="vertical_relaxed")
        .join(acres, on="irwin_id", how="left")
        .sort(["cluster", "rank", "score", "acres_sort", "irwin_id"], descending=[False, False, False, True, False])
        .unique("cluster", keep="first", maintain_order=True)
    )
    return best.select(
        "cluster",
        pl.col("irwin_id").alias("incident_irwin_id"),
        pl.col("how").alias("incident_link"),
        pl.col("km").round(3).alias("incident_distance_km"),
    ).join(table.select(
        pl.col("irwin_id").alias("incident_irwin_id"),
        pl.col("name").alias("incident_name"),
        pl.col("type").alias("incident_type"),
        pl.col("acres").alias("incident_acres"),
        pl.col("percent_contained").alias("incident_percent_contained"),
        pl.col("discovered").alias("incident_discovered"),
    ), on="incident_irwin_id", how="left")


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    det = firms.cluster(firms.mark_duplicates(firms.detections(raw)))
    if det.is_empty():
        return pl.DataFrame(schema=SCHEMA)
    ev = events(det)
    places = admin1.regions(raw["admin1"], ev.select(pl.col("cluster").alias("pid"), "latitude", "longitude"))
    ev = ev.join(places.rename({"pid": "cluster"}), on="cluster").filter(pl.col("country").is_not_null() & pl.col("region").is_not_null())
    det = det.join(ev.select("cluster"), on="cluster", how="semi")
    links = link(det, ev, raw)
    out = ev.join(links, on="cluster", how="left")
    ids = [
        firms.event_id(sat, t.strftime(ISO), lat, lon)
        for sat, t, lat, lon in out.select("first_sat", "first_t", "first_lat", "first_lon").iter_rows()
    ]
    extent = haversine_km(pl.col("min_latitude"), pl.col("min_longitude"), pl.col("max_latitude"), pl.col("max_longitude"))
    out = out.with_columns(
        pl.Series("event_id", ids, dtype=pl.Utf8),
        ((pl.col("days_detected") >= STATIC_MIN_DAYS) & (extent <= STATIC_MAX_EXTENT_KM) & pl.col("incident_irwin_id").is_null()).alias(
            "likely_static_source"
        ),
        pl.col("first").dt.strftime(ISO).alias("first_detection"),
        pl.col("last").dt.strftime(ISO).alias("last_detection"),
    )
    return out.select([pl.col(name).cast(dtype) for name, dtype in SCHEMA.items()])
