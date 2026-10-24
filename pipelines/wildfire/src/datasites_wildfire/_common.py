"""Shared pieces of the wildfiredata pipelines: NIFC WFIGS (ArcGIS) paging, time and id helpers,
spherical geometry and a vectorized point-in-polygon test.

Geometry runs on polars frames, never on per-point Python loops, so a peak-season week (hundreds of
thousands of FIRMS detections, thousands of perimeters) stays fast on a CI runner.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlencode

import polars as pl

EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius
KM_PER_DEG = EARTH_RADIUS_KM * math.pi / 180  # ~111.195 km per degree of latitude (or of longitude at the equator)

# --- NIFC WFIGS ------------------------------------------------------------------------------------
# Public ArcGIS feature services, no key: https://data-nifc.opendata.arcgis.com/
WFIGS = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
INCIDENTS_LAYER = f"{WFIGS}/WFIGS_Incident_Locations_Current/FeatureServer/0"
PERIMETERS_LAYER = f"{WFIGS}/WFIGS_Interagency_Perimeters_Current/FeatureServer/0"
INCIDENTS_ITEM = "https://data-nifc.opendata.arcgis.com/datasets/nifc::current-wildland-fire-incident-locations"
PERIMETERS_ITEM = "https://data-nifc.opendata.arcgis.com/datasets/nifc::wfigs-current-interagency-fire-perimeters"
MAX_RECORD_COUNT = 2000  # the layers' maxRecordCount

# Perimeters are requested generalized: 5 decimals (~1 m) and a 0.0001 degree (~10 m) maximum offset.
# Full-resolution geometry is ~10x larger (19 MB for 104 perimeters on 2026-10-24) with no visible
# difference at the scale fire perimeters are mapped.
PERIMETER_GEOMETRY = {"geometryPrecision": 5, "maxAllowableOffset": 0.0001}


def query_url(layer: str, params: dict[str, Any]) -> str:
    """A layer query URL with its parameters in a fixed order (stable URLs keep conditional GETs useful)."""
    return f"{layer}/query?{urlencode(list(params.items()))}"


def page_count(count: int, page_size: int) -> int:
    """Pages needed for `count` records plus a 10% margin, so records added between the count request and
    the page requests are still fetched. The last page must come back short (see read_pages)."""
    return max(1, -(-(count + max(1, page_size // 10)) // page_size))


def page_urls(ctx: Any, layer: str, params: dict[str, Any], page_size: int) -> dict[str, str]:
    """resultOffset paging, ordered by OBJECTID so pages don't overlap. One count request first."""
    counted = ctx.get_json(f"{layer}/query", params={"where": params.get("where", "1=1"), "returnCountOnly": "true", "f": "json"})
    if "error" in counted or "count" not in counted:
        raise RuntimeError(f"WFIGS count request failed: {json.dumps(counted)[:300]}")
    urls = {}
    for page in range(page_count(int(counted["count"]), page_size)):
        query = {
            "where": "1=1",
            **params,
            "orderByFields": "OBJECTID ASC",
            "resultOffset": page * page_size,
            "resultRecordCount": page_size,
        }
        urls[f"page-{page:03d}"] = query_url(layer, query)
    return urls


def read_pages(raw: dict[str, bytes], prefix: str = "page-") -> list[dict[str, Any]]:
    """Features of every page, in page order, without duplicate OBJECTIDs.

    ArcGIS reports errors with HTTP 200 and an {"error": ...} body, so each page is checked. The last page
    must not report exceededTransferLimit: if it does, the layer grew past the margin while we paged."""
    names = sorted(n for n in raw if n.startswith(prefix))
    if not names:
        raise ValueError("no WFIGS pages were fetched")
    features: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, name in enumerate(names):
        page = json.loads(raw[name])
        if "error" in page:
            raise RuntimeError(f"WFIGS {name} returned an error: {json.dumps(page['error'])[:300]}")
        if "features" not in page:
            raise ValueError(f"WFIGS {name} has no 'features' member")
        exceeded = page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")
        if exceeded and i == len(names) - 1:
            raise RuntimeError("WFIGS layer grew while paging (the last page is still full); the next run will retry")
        for feature in page["features"]:
            attrs = feature.get("attributes") or feature.get("properties") or {}
            oid = attrs.get("OBJECTID", feature.get("id"))
            if oid is None:
                raise ValueError(f"WFIGS {name}: feature without OBJECTID")
            if oid in seen:
                continue
            seen.add(oid)
            features.append(feature)
    return features


def irwin(value: Any) -> str | None:
    """'{8AD4B895-5E4E-4995-82D0-C0F5078AF365}' -> '8AD4B895-5E4E-4995-82D0-C0F5078AF365'."""
    if value is None:
        return None
    text = str(value).strip().strip("{}").upper()
    return text or None


def iso_ms(value: Any) -> str | None:
    """ArcGIS epoch milliseconds (UTC) -> '2026-10-24T13:05:32Z'."""
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def us_state(value: Any) -> str | None:
    """WFIGS POOState 'US-WA' -> 'us-wa' (the repo's US state ids)."""
    if not value:
        return None
    text = str(value).strip().lower()
    return text if len(text) == 5 and text.startswith("us-") else None


def number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def dumps(value: Any) -> bytes:
    """Deterministic compact JSON (GeoJSON outputs)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


# --- spherical geometry ----------------------------------------------------------------------------


def unit_xyz(lat: pl.Expr, lon: pl.Expr) -> list[pl.Expr]:
    """Earth-centred coordinates in km. Grids built on these have no dateline or polar seams."""
    phi, lam = lat.radians(), lon.radians()
    return [
        (EARTH_RADIUS_KM * phi.cos() * lam.cos()).alias("ex"),
        (EARTH_RADIUS_KM * phi.cos() * lam.sin()).alias("ey"),
        (EARTH_RADIUS_KM * phi.sin()).alias("ez"),
    ]


def cell_keys(size_km: float, prefix: str) -> list[pl.Expr]:
    """Integer 3D grid cell of side size_km for the ex/ey/ez columns."""
    return [(pl.col(axis) / size_km).floor().cast(pl.Int64).alias(f"{prefix}{axis[1]}") for axis in ("ex", "ey", "ez")]


def haversine_km(lat1: pl.Expr, lon1: pl.Expr, lat2: pl.Expr, lon2: pl.Expr) -> pl.Expr:
    p1, p2 = lat1.radians(), lat2.radians()
    dphi = p2 - p1
    dlam = (lon2 - lon1).radians()
    a = (dphi / 2).sin().pow(2) + p1.cos() * p2.cos() * (dlam / 2).sin().pow(2)
    return 2 * EARTH_RADIUS_KM * a.sqrt().clip(0.0, 1.0).arcsin()


def wrap_lon(lon: pl.Expr) -> pl.Expr:
    """Longitude into [-180, 180)."""
    return ((lon + 180.0) % 360.0) - 180.0


# --- polygons --------------------------------------------------------------------------------------


def rings_of(geometry: dict[str, Any] | None) -> list[list[list[float]]]:
    """Every ring of a GeoJSON Polygon or MultiPolygon."""
    if not geometry:
        return []
    if geometry["type"] == "Polygon":
        return list(geometry["coordinates"])
    if geometry["type"] == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    return []


def edges_frame(polygons: Iterable[tuple[str, list[list[list[float]]]]]) -> pl.DataFrame:
    """One row per ring edge: poly, x1, y1, x2, y2 (lon/lat degrees). Rings are closed if they aren't."""
    poly: list[str] = []
    x1: list[float] = []
    y1: list[float] = []
    x2: list[float] = []
    y2: list[float] = []
    for pid, rings in polygons:
        for ring in rings:
            n = len(ring)
            if n < 3:
                continue
            closed = ring[0][0] == ring[-1][0] and ring[0][1] == ring[-1][1]
            count = n - 1 if closed else n  # number of edges
            for i in range(count):
                a, b = ring[i], ring[(i + 1) % n]
                poly.append(pid)
                x1.append(float(a[0]))
                y1.append(float(a[1]))
                x2.append(float(b[0]))
                y2.append(float(b[1]))
    return pl.DataFrame(
        {"poly": poly, "x1": x1, "y1": y1, "x2": x2, "y2": y2},
        schema={"poly": pl.Utf8, "x1": pl.Float64, "y1": pl.Float64, "x2": pl.Float64, "y2": pl.Float64},
    )


class PolygonIndex:
    """Even-odd point-in-polygon and point-to-boundary distance over many polygons, vectorized.

    Candidate polygons come from a tile grid on their bounding boxes; the crossing test then only looks
    at a polygon's edges in the point's latitude band. Holes and multi-part polygons work because the
    even-odd rule counts every ring of a polygon together."""

    def __init__(self, edges: pl.DataFrame, *, tile_deg: float, band_deg: float):
        self.tile = tile_deg
        self.band = band_deg
        self.edges = edges
        self.boxes = edges.group_by("poly").agg(
            pl.min_horizontal("x1", "x2").min().alias("minx"),
            pl.max_horizontal("x1", "x2").max().alias("maxx"),
            pl.min_horizontal("y1", "y2").min().alias("miny"),
            pl.max_horizontal("y1", "y2").max().alias("maxy"),
        )
        self.banded = edges.with_columns(
            pl.int_ranges(
                (pl.min_horizontal("y1", "y2") / band_deg).floor().cast(pl.Int64),
                (pl.max_horizontal("y1", "y2") / band_deg).floor().cast(pl.Int64) + 1,
            ).alias("band")
        ).explode("band")

    def _candidates(self, points: pl.DataFrame, pad_deg: float = 0.0) -> pl.DataFrame:
        """(pid, x, y, poly) for every polygon whose padded bounding box holds the point."""
        tiles = self.boxes.with_columns(
            pl.int_ranges(((pl.col("minx") - pad_deg) / self.tile).floor().cast(pl.Int64), ((pl.col("maxx") + pad_deg) / self.tile).floor().cast(pl.Int64) + 1).alias("tx"),
            pl.int_ranges(((pl.col("miny") - pad_deg) / self.tile).floor().cast(pl.Int64), ((pl.col("maxy") + pad_deg) / self.tile).floor().cast(pl.Int64) + 1).alias("ty"),
        ).explode("tx").explode("ty")
        located = points.with_columns(
            (pl.col("x") / self.tile).floor().cast(pl.Int64).alias("tx"),
            (pl.col("y") / self.tile).floor().cast(pl.Int64).alias("ty"),
        )
        joined = located.join(tiles, on=["tx", "ty"], how="inner")
        return joined.filter(
            (pl.col("x") >= pl.col("minx") - pad_deg)
            & (pl.col("x") <= pl.col("maxx") + pad_deg)
            & (pl.col("y") >= pl.col("miny") - pad_deg)
            & (pl.col("y") <= pl.col("maxy") + pad_deg)
        ).select("pid", "x", "y", "poly")

    def contains(self, points: pl.DataFrame) -> pl.DataFrame:
        """points: pid, x (lon), y (lat). Returns (pid, poly) for every polygon containing the point."""
        empty = pl.DataFrame(schema={"pid": points.schema["pid"], "poly": pl.Utf8})
        if points.is_empty() or self.edges.is_empty():
            return empty
        cand = self._candidates(points).with_columns((pl.col("y") / self.band).floor().cast(pl.Int64).alias("band"))
        hits = cand.join(self.banded, on=["poly", "band"], how="inner").filter(
            (pl.col("y1") > pl.col("y")) != (pl.col("y2") > pl.col("y"))
        ).filter(
            pl.col("x") < (pl.col("x2") - pl.col("x1")) * (pl.col("y") - pl.col("y1")) / (pl.col("y2") - pl.col("y1")) + pl.col("x1")
        )
        counts = hits.group_by("pid", "poly").len()
        return counts.filter(pl.col("len") % 2 == 1).select("pid", "poly")

    def near(self, points: pl.DataFrame, max_km: float) -> pl.DataFrame:
        """(pid, poly, km): distance from each point to the boundary of every polygon within max_km."""
        empty = pl.DataFrame(schema={"pid": points.schema["pid"], "poly": pl.Utf8, "km": pl.Float64})
        if points.is_empty() or self.edges.is_empty():
            return empty
        pad = max_km / KM_PER_DEG
        # Longitude degrees shrink with latitude; 1/cos(85 deg) bounds the padding up to the Arctic islands.
        cand = self._candidates(points, pad_deg=pad / math.cos(math.radians(85)))
        cand = cand.with_columns(
            pl.int_ranges(
                ((pl.col("y") - pad) / self.band).floor().cast(pl.Int64),
                ((pl.col("y") + pad) / self.band).floor().cast(pl.Int64) + 1,
            ).alias("band")
        ).explode("band")
        pairs = cand.join(self.banded, on=["poly", "band"], how="inner").unique(["pid", "poly", "x1", "y1", "x2", "y2"])
        # Point-to-segment distance in a local equirectangular projection around the point (km).
        k = KM_PER_DEG
        coslat = pl.col("y").radians().cos()
        ax = (pl.col("x1") - pl.col("x")) * coslat * k
        ay = (pl.col("y1") - pl.col("y")) * k
        bx = (pl.col("x2") - pl.col("x")) * coslat * k
        by = (pl.col("y2") - pl.col("y")) * k
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = pl.when(length2 > 0).then(((-ax * dx) - (ay * dy)) / length2).otherwise(0.0).clip(0.0, 1.0)
        px, py = ax + t * dx, ay + t * dy
        dist = pairs.with_columns((px * px + py * py).sqrt().alias("km"))
        return dist.group_by("pid", "poly").agg(pl.col("km").min()).filter(pl.col("km") <= max_km)
