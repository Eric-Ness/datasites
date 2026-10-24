"""smoke: NOAA NESDIS Hazard Mapping System (HMS) smoke polygons for the last 7 days, as GeoJSON.

HMS analysts draw smoke plumes on GOES and polar-orbiter imagery and label each polygon Light, Medium
or Heavy. These are visual density categories of smoke anywhere in the column, not surface
concentrations. One zipped shapefile per day (UTC), updated through the day and finalized the next
morning: https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile/YYYY/MM/

Shapefile fields: Satellite, Start, End ("YYYYDDD HHMM", UTC day-of-year), Density.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import shapefile

from ._common import dumps

BASE = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile"
DAYS = 7
DENSITIES = ("Light", "Medium", "Heavy")
LISTING_ROW = re.compile(r'href="(hms_smoke(\d{8})\.zip)".*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', re.S)


def _month_url(day: date) -> str:
    return f"{BASE}/{day:%Y}/{day:%m}/"


def resources(ctx) -> dict[str, str]:
    """The newest 7 daily files (by the date in their name) listed in this month's and last month's folders,
    plus those folder listings (they carry each file's update time)."""
    today = ctx.now.astimezone(timezone.utc).date()
    months = sorted({today.replace(day=1), (today.replace(day=1) - timedelta(days=1)).replace(day=1)})
    urls: dict[str, str] = {}
    found: dict[str, str] = {}
    for month in months:
        url = _month_url(month)
        try:
            listing = ctx.get(url)
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 404:  # a new month's folder appears with its first file
                continue
            raise
        urls[f"listing-{month:%Y-%m}"] = url
        for name, stamp, _modified in LISTING_ROW.findall(listing.decode("utf-8", "replace")):
            if stamp <= f"{today:%Y%m%d}":
                found[stamp] = url + name
    for stamp in sorted(found)[-DAYS:]:
        urls[f"smoke-{stamp}"] = found[stamp]
    if not any(name.startswith("smoke-") for name in urls):
        raise RuntimeError("no HMS smoke files listed for this or last month")
    return urls


def _time(value: Any) -> str | None:
    """'2026295 1800' -> '2026-10-22T18:00:00Z'."""
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{4})(\d{3})\s+(\d{2})(\d{2})", text)
    if not m:
        return None
    year, doy, hh, mm = (int(g) for g in m.groups())
    moment = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1, hours=hh, minutes=mm)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ring(points: list[list[float]], reverse: bool) -> list[list[float]]:
    ring = [[round(float(x), 4), round(float(y), 4)] for x, y in (p[:2] for p in points)]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring[::-1] if reverse else ring


def _signed_area(ring: list[list[float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / 2


def _geometry(shape: shapefile.Shape) -> dict[str, Any] | None:
    """Shapefile rings (outer clockwise, holes counter-clockwise) -> RFC 7946 (outer counter-clockwise)."""
    bounds = list(shape.parts) + [len(shape.points)]
    rings = [_ring(shape.points[bounds[i] : bounds[i + 1]], reverse=True) for i in range(len(shape.parts))]
    rings = [r for r in rings if len(r) >= 4]
    if not rings:
        return None
    polygons: list[list[list[list[float]]]] = []
    for ring in rings:
        if _signed_area(ring) >= 0 or not polygons:  # counter-clockwise now: an outer ring
            polygons.append([ring])
        else:
            polygons[-1].append(ring)  # a hole of the preceding outer ring
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def read_day(body: bytes) -> list[dict[str, Any]]:
    z = zipfile.ZipFile(io.BytesIO(body))
    by_ext = {n.rsplit(".", 1)[-1].lower(): n for n in z.namelist()}
    missing = [e for e in ("shp", "shx", "dbf") if e not in by_ext]
    if missing:
        raise ValueError(f"HMS zip lacks .{', .'.join(missing)}")
    reader = shapefile.Reader(
        shp=io.BytesIO(z.read(by_ext["shp"])), shx=io.BytesIO(z.read(by_ext["shx"])), dbf=io.BytesIO(z.read(by_ext["dbf"]))
    )
    fields = [f[0] for f in reader.fields[1:]]
    for needed in ("Satellite", "Start", "End", "Density"):
        if needed not in fields:
            raise ValueError(f"HMS shapefile has no {needed} field (fields: {fields})")
    features = []
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record.as_dict()
        geometry = _geometry(shape_record.shape)
        if geometry is None:
            continue
        density = str(record["Density"] or "").strip().title() or None
        if density is not None and density not in DENSITIES:
            raise ValueError(f"unexpected HMS density {record['Density']!r}")
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "satellite": str(record["Satellite"] or "").strip() or None,
                "start": _time(record["Start"]),
                "end": _time(record["End"]),
                "density": density,
            },
        })
    return features


def _listing_times(raw: dict[str, bytes]) -> dict[str, str]:
    times = {}
    for name, body in raw.items():
        if name.startswith("listing-"):
            for _file, stamp, modified in LISTING_ROW.findall(body.decode("utf-8", "replace")):
                times[stamp] = modified.replace(" ", "T") + ":00Z"  # the server lists UTC times
    return times


def build(raw: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, Any]]:
    updated = _listing_times(raw)
    days = sorted(name[len("smoke-"):] for name in raw if name.startswith("smoke-"))
    if not days:
        raise ValueError("no HMS smoke days")
    files: dict[str, bytes] = {}
    index = []
    by_day: dict[str, list] = {}
    for stamp in days:
        features = read_day(raw[f"smoke-{stamp}"])
        day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
        by_day[day] = features
        path = f"days/{day}.geojson"
        files[path] = dumps({"type": "FeatureCollection", "date": day, "features": features})
        counts = {d.lower(): sum(1 for f in features if f["properties"]["density"] == d) for d in DENSITIES}
        index.append({"date": day, "path": path, "polygons": len(features), **counts, "file_updated_at": updated.get(stamp)})
    with_smoke = [d for d in by_day if by_day[d]]
    latest = with_smoke[-1] if with_smoke else max(by_day)
    files["latest.geojson"] = dumps({"type": "FeatureCollection", "date": latest, "features": by_day[latest]})
    files["index.json"] = dumps({
        "about": "NOAA HMS analyst-drawn smoke polygons by UTC day. Density is a visual category (Light, Medium, "
                 "Heavy) of smoke anywhere in the column, not a surface concentration. The newest day is updated "
                 "through the day and finalized the next morning; latest.geojson is the newest day with smoke.",
        "latest": latest,
        "days": index,
    })
    return files, {"rows": sum(len(f) for f in by_day.values()), "days": len(days)}
