"""fire-perimeters: NIFC WFIGS current interagency fire perimeters, plus an archive of every snapshot.

WFIGS drops fires from its "current" layer under fall-off rules (3 to 14 days without an update), so the
day-by-day growth of a perimeter is not kept anywhere public. This dataset keeps it:

    current.geojson                               the current perimeters (generalized to ~10 m)
    perimeters/<IRWIN>/<YYYYMMDDTHHMMSSZ>-<hash>.geojson
                                                  one file per distinct perimeter shape, written once
    perimeters/index.json                         every perimeter shape ever archived, by incident
    snapshots/<YYYY-MM-DD>/<HHMMSSZ>-<hash>.json  which perimeter shapes were current at that moment
    snapshots/index.json                          every snapshot ever archived

A snapshot is stamped with the newest edit time in the data (not the clock), so an unchanged layer gives
no new snapshot. Perimeter shapes are stored once and referenced, so a run where three fires grew adds
three small files rather than a copy of every perimeter. Files from the last 14 days stay in the
current version; older ones stay in earlier versions (GET /v1/fire-perimeters@<version>/<path>), and
their index entries keep the SHA-256 of the immutable blob.

To carry the archive forward, collect() lists the previously published version's archive files (path ->
blob hash, from ctx.previous_files()) and build(raw, ctx) reads those immutable blobs with ctx.read_blob.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from ._common import (
    MAX_RECORD_COUNT,
    PERIMETER_GEOMETRY,
    PERIMETERS_LAYER,
    dumps,
    irwin,
    iso_ms,
    number,
    page_urls,
    read_pages,
    rings_of,
    us_state,
)

PAGE_SIZE = 250
assert PAGE_SIZE <= MAX_RECORD_COUNT
FIELDS = [
    "OBJECTID", "poly_IRWINID", "poly_IncidentName", "poly_FeatureCategory", "poly_MapMethod", "poly_GISAcres",
    "poly_PolygonDateTime", "poly_DateCurrent", "poly_Source", "attr_IrwinID", "attr_IncidentName",
    "attr_UniqueFireIdentifier", "attr_IncidentTypeCategory", "attr_IncidentSize", "attr_PercentContained",
    "attr_FireDiscoveryDateTime", "attr_POOState", "attr_GACC", "attr_ModifiedOnDateTime_dt",
]
QUERY = {"outFields": ",".join(FIELDS), "outSR": 4326, **PERIMETER_GEOMETRY, "f": "geojson"}
RETAIN = timedelta(days=14)
PREVIOUS = "previous.json"


# --- collecting --------------------------------------------------------------------------------------


def previous_listing(ctx) -> bytes | None:
    """The published version's archive files as {path: blob hash}. Blobs are immutable, so this small
    listing pins the exact bytes build() reads, and it is the only previous-state input in raw."""
    archive = {path: entry["blob"] for path, entry in sorted(ctx.previous_files().items()) if path.startswith(("perimeters/", "snapshots/"))}
    return dumps({"files": archive}) if archive else None


def collect(ctx) -> dict[str, bytes]:
    raw = {name: ctx.get(url) for name, url in page_urls(ctx, PERIMETERS_LAYER, QUERY, PAGE_SIZE).items()}
    listing = previous_listing(ctx)
    if listing is not None:
        raw[PREVIOUS] = listing
    return raw


def normalize(name: str, body: bytes) -> bytes:
    """Only the WFIGS pages decide whether anything changed; the previous archive is carried along."""
    return b"" if name == PREVIOUS else body


def _load_previous(raw: dict[str, bytes], ctx) -> tuple[dict[str, str], dict[str, bytes]]:
    """({path: blob hash}, {path: bytes}) of the previous version's archive, read in parallel by hash."""
    if PREVIOUS not in raw:
        return {}, {}
    listing = json.loads(raw[PREVIOUS])["files"]
    if ctx is None:
        raise RuntimeError("fire-perimeters: the previous archive can only be read inside a datasites run")
    with ThreadPoolExecutor(max_workers=16) as pool:
        loaded = dict(zip(listing, pool.map(ctx.read_blob, listing.values())))
    return listing, {path: body for path, body in loaded.items() if body is not None}


# --- building ----------------------------------------------------------------------------------------


def _stamp(iso: str) -> str:
    return iso.replace("-", "").replace(":", "")  # 2026-10-24T13:05:32Z -> 20261024T130532Z


def _geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    """Coordinates rounded to 5 decimals (the request already asked for that) so hashes are stable."""
    def walk(c):
        if isinstance(c, (int, float)):
            return round(float(c), 5)
        return [walk(x) for x in c]

    return {"type": geometry["type"], "coordinates": walk(geometry["coordinates"])}


def _properties(p: dict[str, Any], incident: str) -> dict[str, Any]:
    return {
        "irwin_id": incident,
        "incident_name": p.get("attr_IncidentName") or p.get("poly_IncidentName"),
        "unique_fire_id": p.get("attr_UniqueFireIdentifier"),
        "incident_type": p.get("attr_IncidentTypeCategory"),
        "state": us_state(p.get("attr_POOState")),
        "gacc": p.get("attr_GACC"),
        "gis_acres": round(number(p.get("poly_GISAcres")), 2) if number(p.get("poly_GISAcres")) is not None else None,
        "incident_acres": number(p.get("attr_IncidentSize")),
        "percent_contained": number(p.get("attr_PercentContained")),
        "discovered": iso_ms(p.get("attr_FireDiscoveryDateTime")),
        "perimeter_datetime": iso_ms(p.get("poly_PolygonDateTime")),
        "date_current": iso_ms(p.get("poly_DateCurrent")),
        "incident_modified": iso_ms(p.get("attr_ModifiedOnDateTime_dt")),
        "feature_category": p.get("poly_FeatureCategory"),
        "map_method": p.get("poly_MapMethod"),
        "source": p.get("poly_Source"),
    }


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def build(raw: dict[str, bytes], ctx=None) -> tuple[dict[str, bytes], dict[str, Any]]:
    features = read_pages(raw)
    prev_blobs, prev_files = _load_previous(raw, ctx)
    prev_perimeters = json.loads(prev_files.pop("perimeters/index.json"))["perimeters"] if "perimeters/index.json" in prev_files else []
    prev_snapshots = json.loads(prev_files.pop("snapshots/index.json"))["snapshots"] if "snapshots/index.json" in prev_files else []

    # The archive so far: perimeter shape -> its index entry.
    shapes: dict[tuple[str, str], dict[str, Any]] = {(e["irwin_id"], e["shape"]): dict(e) for e in prev_perimeters}
    snapshots: dict[str, dict[str, Any]] = {e["path"]: dict(e) for e in prev_snapshots}
    files: dict[str, bytes] = {}

    current = []
    skipped = 0
    newest = None
    for feature in features:
        p = feature.get("properties") or {}
        incident = irwin(p.get("poly_IRWINID")) or irwin(p.get("attr_IrwinID"))
        geometry = feature.get("geometry")
        if incident is None or not rings_of(geometry):
            skipped += 1
            continue
        geometry = _geometry(geometry)
        props = _properties(p, incident)
        shape = hashlib.sha256(dumps(geometry)).hexdigest()[:16]
        edited = max(filter(None, (props["date_current"], props["incident_modified"])), default=None)
        if edited and (newest is None or edited > newest):
            newest = edited
        current.append((incident, shape, geometry, props))
    current.sort(key=lambda c: (c[0], c[1]))

    members = []
    for incident, shape, geometry, props in current:
        key = (incident, shape)
        entry = shapes.get(key)
        if entry is None:
            stamp = props["date_current"] or props["perimeter_datetime"] or newest or "1970-01-01T00:00:00Z"
            path = f"perimeters/{incident}/{_stamp(stamp)}-{shape[:8]}.geojson"
            entry = {
                "irwin_id": incident,
                "incident_name": props["incident_name"],
                "shape": shape,
                "path": path,
                "first_seen": newest,
                "perimeter_datetime": props["perimeter_datetime"],
                "gis_acres": props["gis_acres"],
                "blob": None,
            }
            shapes[key] = entry
            files[path] = dumps({"type": "Feature", "geometry": geometry, "properties": {**props, "first_seen": newest}})
        members.append({
            "irwin_id": incident,
            "incident_name": props["incident_name"],
            "gis_acres": props["gis_acres"],
            "incident_acres": props["incident_acres"],
            "percent_contained": props["percent_contained"],
            "date_current": props["date_current"],
            "path": entry["path"],
        })

    if members and newest:
        digest = hashlib.sha256(dumps(members)).hexdigest()[:8]
        day, clock = newest[:10], newest[11:].replace(":", "")
        snap_path = f"snapshots/{day}/{clock}-{digest}.json"
        if snap_path not in snapshots:
            snapshots[snap_path] = {"path": snap_path, "snapshot_at": newest, "perimeters": len(members),
                                    "gis_acres": round(sum(m["gis_acres"] or 0 for m in members), 2), "blob": None}
            files[snap_path] = dumps({"snapshot_at": newest, "perimeters": members})

    # Retention: the last 14 days of snapshots (from the newest), and every shape they reference.
    latest = max((e["snapshot_at"] for e in snapshots.values()), default=None)
    cutoff = (_parse_iso(latest) - RETAIN).strftime("%Y-%m-%dT%H:%M:%SZ") if latest else None
    keep_snapshots = {p for p, e in snapshots.items() if cutoff is None or e["snapshot_at"] >= cutoff}
    referenced = {m["path"] for m in members}
    for path in sorted(keep_snapshots):
        body = files.get(path) or prev_files.get(path)
        if body is None:
            continue  # left the current version earlier; its blob stays in the bucket
        files[path] = body
        referenced.update(m["path"] for m in json.loads(body)["perimeters"])
    for path in sorted(referenced):
        if path not in files and path in prev_files:
            files[path] = prev_files[path]
    for entry in [*shapes.values(), *snapshots.values()]:
        entry["retained"] = entry["path"] in files
        if not entry["retained"] and entry.get("blob") is None:
            # Leaving the current version now: record the immutable blob that holds it.
            entry["blob"] = prev_blobs.get(entry["path"])

    current_fc = {
        "type": "FeatureCollection",
        "snapshot_at": newest,
        "features": [
            {"type": "Feature", "geometry": geometry, "properties": {**props, "perimeter_path": shapes[(incident, shape)]["path"]}}
            for incident, shape, geometry, props in current
        ],
    }
    files["current.geojson"] = dumps(current_fc)
    files["perimeters/index.json"] = dumps({
        "about": "Every distinct perimeter shape archived from WFIGS current perimeters. Where retained is false, "
                 "the file is in earlier versions of this dataset (GET /v1/fire-perimeters/versions, then "
                 "/v1/fire-perimeters@<version>/<path>); blob is its SHA-256.",
        "perimeters": sorted(shapes.values(), key=lambda e: (e["irwin_id"], e["first_seen"] or "", e["path"])),
    })
    files["snapshots/index.json"] = dumps({
        "about": "Every archived snapshot of WFIGS current perimeters, stamped with the newest edit time in the "
                 "data. Where retained is false, the file is in earlier versions; blob is its SHA-256.",
        "snapshots": sorted(snapshots.values(), key=lambda e: (e["snapshot_at"], e["path"])),
    })
    stats = {
        "rows": len(current),
        "skipped_without_geometry_or_irwin": skipped,
        "snapshots": len(snapshots),
        "shapes": len(shapes),
        "retained_files": sum(1 for p in files if p.startswith(("perimeters/", "snapshots/")) and not p.endswith("index.json")),
    }
    return files, stats
