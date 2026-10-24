"""States and provinces for fire events, from Natural Earth's 1:10m admin-1 polygons (public domain).

Only US states, Canadian provinces and territories are published; polygons of the neighbours the FIRMS
region boxes reach into (Mexico, Greenland, Cuba, the Bahamas, Saint Pierre and Miquelon, Russia's far
east) are loaded too, so a fire just across a border is recognized as outside rather than snapped to
the nearest US or Canadian polygon. A point in no polygon (a coastline the 1:10m outline simplifies, a
small island) takes the nearest polygon within 10 km; farther out (offshore platforms) it has none.
"""

from __future__ import annotations

import io
import math
import zipfile
from functools import lru_cache
from hashlib import sha256

import polars as pl
import shapefile

from datasites_core import geo

from ._common import KM_PER_DEG, PolygonIndex, edges_frame, wrap_lon

URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"
STEM = "ne_10m_admin_1_states_provinces"
LOADED_COUNTRIES = {"USA", "CAN", "MEX", "GRL", "CUB", "BHS", "SPM", "RUS"}
PUBLISHED = {"USA": "usa", "CAN": "can"}
# ISO 3166-2:CA names (Natural Earth spells Quebec with an accent and Yukon as "Yukon"; these are the
# official English short names).
CANADA = {
    "ca-ab": "Alberta", "ca-bc": "British Columbia", "ca-mb": "Manitoba", "ca-nb": "New Brunswick",
    "ca-nl": "Newfoundland and Labrador", "ca-ns": "Nova Scotia", "ca-nt": "Northwest Territories",
    "ca-nu": "Nunavut", "ca-on": "Ontario", "ca-pe": "Prince Edward Island", "ca-qc": "Quebec",
    "ca-sk": "Saskatchewan", "ca-yt": "Yukon",
}
SEARCH_KM = (2.0, 5.0, 10.0)
DIRECTIONS = 8


def _reader(body: bytes) -> shapefile.Reader:
    z = zipfile.ZipFile(io.BytesIO(body))
    names = {n.rsplit("/", 1)[-1]: n for n in z.namelist()}
    try:
        parts = {ext: io.BytesIO(z.read(names[f"{STEM}.{ext}"])) for ext in ("shp", "shx", "dbf")}
    except KeyError as err:
        raise ValueError(f"Natural Earth zip is missing {err}") from None
    return shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding="utf-8")


def _rings(shape: shapefile.Shape) -> list[list[list[float]]]:
    bounds = list(shape.parts) + [len(shape.points)]
    return [[list(p) for p in shape.points[bounds[i] : bounds[i + 1]]] for i in range(len(shape.parts))]


@lru_cache(maxsize=2)
def _load(digest: str, body: bytes) -> tuple[PolygonIndex, dict[str, tuple[str | None, str | None, str | None]]]:
    """(polygon index, poly -> (country, region id, region name)). Cached per zip content."""
    del digest
    reader = _reader(body)
    fields = [f[0] for f in reader.fields[1:]]
    a3, iso = fields.index("adm0_a3"), fields.index("iso_3166_2")
    polygons = []
    meta: dict[str, tuple[str | None, str | None, str | None]] = {}
    for i, record in enumerate(reader.iterRecords(fields=None)):
        country = record[a3]
        if country not in LOADED_COUNTRIES:
            continue
        code = str(record[iso]).lower()
        poly = f"ne-{i}"
        if country == "USA":
            location = geo.location(code)
            meta[poly] = ("usa", code, location["name"]) if location else ("usa", None, None)
        elif country == "CAN":
            meta[poly] = ("can", code, CANADA.get(code)) if code in CANADA else ("can", None, None)
        else:
            meta[poly] = (None, None, None)  # loaded only to recognize "not in the US or Canada"
        polygons.append((poly, _rings(reader.shape(i))))
    if not any(m[0] == "usa" for m in meta.values()) or not any(m[0] == "can" for m in meta.values()):
        raise ValueError("Natural Earth admin-1 file has no US or Canadian polygons")
    return PolygonIndex(edges_frame(polygons), tile_deg=1.0, band_deg=0.1), meta


def regions(body: bytes, points: pl.DataFrame) -> pl.DataFrame:
    """points: pid, latitude, longitude -> pid, country, region, region_name (null when outside the US and Canada)."""
    index, meta = _load(sha256(body).hexdigest(), body)
    lookup = pl.DataFrame(
        {
            "poly": list(meta),
            "country": [m[0] for m in meta.values()],
            "region": [m[1] for m in meta.values()],
            "region_name": [m[2] for m in meta.values()],
        },
        schema={"poly": pl.Utf8, "country": pl.Utf8, "region": pl.Utf8, "region_name": pl.Utf8},
    )
    pts = points.select("pid", pl.col("longitude").alias("x"), pl.col("latitude").alias("y"))
    inside = index.contains(pts).sort("pid", "poly").unique("pid", keep="first", maintain_order=True)
    # Not inside any polygon: probe 8 directions at 2, 5 and 10 km and take the nearest ring that hits.
    missing = pts.join(inside, on="pid", how="anti")
    found = [inside.with_columns(pl.lit(0.0).alias("dist"), pl.lit(0).alias("dir"))]
    for km in SEARCH_KM:
        if missing.is_empty():
            break
        probes = pl.concat(
            [
                missing.select(
                    "pid",
                    wrap_lon(pl.col("x") + km * math.sin(2 * math.pi * d / DIRECTIONS) / (KM_PER_DEG * pl.col("y").radians().cos())).alias("x"),
                    (pl.col("y") + km * math.cos(2 * math.pi * d / DIRECTIONS) / KM_PER_DEG).clip(-90, 90).alias("y"),
                    pl.lit(d).alias("dir"),
                )
                for d in range(DIRECTIONS)
            ]
        ).with_row_index("probe")
        hits = index.contains(probes.select(pl.col("probe").alias("pid"), "x", "y"))
        hits = hits.rename({"pid": "probe"}).join(probes.select("probe", "pid", "dir"), on="probe")
        best = hits.sort("pid", "dir", "poly").unique("pid", keep="first", maintain_order=True)
        found.append(best.select("pid", "poly", pl.lit(km).alias("dist"), "dir"))
        missing = missing.join(best, on="pid", how="anti")
    located = pl.concat([f.select("pid", "poly") for f in found])
    out = points.select("pid").join(located, on="pid", how="left").join(lookup, on="poly", how="left")
    return out.select("pid", "country", "region", "region_name")
