"""visualizedata/boundaries: the shared map boundaries every site's maps use.

GeoJSON layers whose feature `id` (and `properties.id`) is the location id from packages/geo
(datasites_core.geo), so a map joins to any dataset of the family by id alone:
  us/states-20m.geojson, us/states-5m.geojson                     us-ca, us-dc, us-pr, ...
  us/counties-20m.geojson, us/counties-5m.geojson                 us-county-06037, ...
  us/congressional-districts-20m.geojson, ...-5m.geojson          us-cd-0601 (119th Congress)
  world/countries-110m.geojson, world/countries-50m.geojson       usa, fra, ... (+ ne-* for features with
                                                                  no ISO 3166-1 code, e.g. ne-kos)
  index.json                                                      every layer: path, level, feature count,
                                                                  source, license, vintage and caveats

Sources (both public domain):
  - U.S. Census Bureau 2025 cartographic boundary files (GENZ2025, posted 2026-04-23), 1:20,000,000 and
    1:5,000,000. The 2025 files are the newest vintage on www2.census.gov/geo/tiger/.
  - Natural Earth admin-0 countries, 1:110m and 1:50m, pinned to the v5.1.2 tag of the natural-earth-vector
    repository, with Natural Earth's default de facto borders.

Both are pinned: a new Census vintage or Natural Earth release is a deliberate code change (bump the
constants below and the package version), reviewed against the location registry.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

import shapefile

from datasites_core import geo

from .geometry import Ring, geometry_json, polygons_from_geojson, polygons_from_shapefile_rings

CENSUS_VINTAGE = 2025
CENSUS_BASE = f"https://www2.census.gov/geo/tiger/GENZ{CENSUS_VINTAGE}/shp/"
CENSUS_PAGE = "https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html"
CENSUS_POSTED = "2026-04-23"
CONGRESS = 119
NE_TAG = "v5.1.2"
NE_BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_TAG}/geojson/"
NE_TERMS = "https://www.naturalearthdata.com/about/terms-of-use/"

CENSUS_ATTRIBUTION = f"Source: U.S. Census Bureau, {CENSUS_VINTAGE} Cartographic Boundary Files"
NE_ATTRIBUTION = "Made with Natural Earth. Free vector and raster map data @ naturalearthdata.com."

# Census state FIPS codes of the Island Areas. The 1:5m files include them; the location registry (the
# 2024 Gazetteer) has them as states but has no county-level ids for them, so their county-equivalents
# are left out of the county layers and listed in index.json.
ISLAND_AREAS = {"60": "American Samoa", "66": "Guam", "69": "Northern Mariana Islands", "78": "U.S. Virgin Islands"}

NON_REGISTRY_PREFIX = "ne-"
# Natural Earth features with no ISO 3166-1 code of their own get "ne-" + their ADM0_A3 code. Notes for
# the ones Natural Earth v5.1.2 has; any other one still gets an id and a generic note.
NE_NOTES = {
    "CYN": "Northern Cyprus: de facto entity with no ISO 3166-1 code (ISO counts the area as part of cyp)",
    "SOL": "Somaliland: de facto entity with no ISO 3166-1 code (ISO counts the area as part of som)",
    "KOS": "Kosovo: no ISO 3166-1 code (XK/XKX is a user-assigned code, not in the registry)",
    "KAS": "Siachen Glacier: disputed between India and Pakistan; Natural Earth maps it separately",
    "IOA": "Indian Ocean Territories: Christmas Island (cxr) and the Cocos (Keeling) Islands (cck) as one "
    "Natural Earth feature",
    "ATC": "Ashmore and Cartier Islands: an Australian external territory with no ISO 3166-1 code of its own",
}

COORDINATES_NOTE = (
    "GeoJSON longitude/latitude in decimal degrees, rounded to 5 decimal places (about 1.1 m). "
    "The US layers keep the Census files' NAD83 geographic coordinates (EPSG:4269) without a datum shift: "
    "NAD83 and WGS 84 differ by about 1-2 m in the conterminous US, far below what a 1:5m or 1:20m file "
    "resolves, so they are published as RFC 7946 coordinates as they are. The world layers are WGS 84 "
    "(Natural Earth's CRS84)."
)
GEOMETRY_NOTE = (
    "Polygon and MultiPolygon features. Exterior rings run counterclockwise and holes clockwise "
    "(the RFC 7946 right-hand rule); every ring is closed; repeated positions left by rounding are removed. "
    "No edge crosses the antimeridian: features that span it (Alaska's Aleutians, Russia, Fiji) are split "
    "at 180 degrees into separate parts, as in the sources, so some US parts have positive longitudes. "
    "Natural Earth's Antarctica closes its ring along the South Pole (latitude -90)."
)
CENSUS_CAVEATS = [
    "Generalized cartographic boundaries for small-scale thematic maps, clipped to a simplified US "
    "shoreline. The Census Bureau says not to use them for area or perimeter calculation, geocoding or "
    "precise geographic relationships.",
    "Some small areas, holes and offshore parts are left out where they are not visible at the target scale.",
]
CD_CAVEATS = [
    f"Districts of the {CONGRESS}th Congress (2025-2027) as the Census Bureau published them. Ten states "
    "redrew their congressional maps for the 2026 elections (AL, CA, FL, LA, MO, NC, OH, TN, TX, UT), so "
    "this layer is out of date for them from the 2026 elections on. 2026 maps from state sources are not "
    "included yet.",
    "Ids are us-cd- plus the Census GEOID (state FIPS + district number). 00 is an at-large district; 98 "
    "is a non-voting delegate or resident commissioner district (DC, Puerto Rico and, at 1:5m, the Island "
    "Areas), marked voting: false.",
]
NE_CAVEATS = [
    "Natural Earth's default de facto borders (the point of view of who administers an area on the ground), "
    "not any one government's official view. For example Crimea is drawn as part of Russia; Kashmir is divided "
    "between India, Pakistan and China along the lines they control (Gilgit in pak, Aksai Chin in chn), with the "
    "Siachen Glacier separate at 1:50m; Western Sahara (esh) is only the area east of the Moroccan berm, so "
    "Laayoune is in mar; Taiwan is twn.",
    "Natural Earth map units, not ISO 3166-1 one to one: some ISO countries are part of another feature "
    "(French Guiana is inside France) or too small for the scale. Registry countries with no feature are "
    "listed per layer.",
    "Ids: ISO_A3_EH, or ADM0_A3 where ISO_A3 is -99, lowercased, so the ids are the registry's country ids. "
    "Features with no registry id get ne- + ADM0_A3 and are listed under not_in_registry.",
]


@dataclass(frozen=True)
class Layer:
    path: str
    resource: str
    url: str
    level: str
    scale: str
    kind: str  # census-state | census-county | census-cd | ne-countries


def _census(level: str, kind: str, slug: str, scale: str) -> Layer:
    name = f"cb_{CENSUS_VINTAGE}_us_{slug}_{scale}"
    return Layer(f"us/{level}-{scale}.geojson", f"{slug}-{scale}", f"{CENSUS_BASE}{name}.zip", level, scale, kind)


def _ne(scale: str) -> Layer:
    url = f"{NE_BASE}ne_{scale}_admin_0_countries.geojson"
    return Layer(f"world/countries-{scale}.geojson", f"ne-countries-{scale}", url, "countries", scale, "ne-countries")


LAYERS: list[Layer] = [
    _census("states", "census-state", "state", "20m"),
    _census("counties", "census-county", "county", "20m"),
    _census("congressional-districts", "census-cd", f"cd{CONGRESS}", "20m"),
    _census("states", "census-state", "state", "5m"),
    _census("counties", "census-county", "county", "5m"),
    _census("congressional-districts", "census-cd", f"cd{CONGRESS}", "5m"),
    _ne("110m"),
    _ne("50m"),
]

# Fails the build if a registry county or state has no boundary in a US layer. Tests that build from a
# small fixture subset switch it off.
REQUIRE_FULL_COVERAGE = True


def resources(ctx) -> dict[str, str]:
    return {layer.resource: layer.url for layer in LAYERS}


# --- reading -------------------------------------------------------------------------------------


def read_shapefile(body: bytes, where: str) -> list[tuple[dict[str, Any], list[list[Ring]]]]:
    """(attributes, polygons) for every record of the one shapefile inside a Census zip."""
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        members = {}
        for name in zf.namelist():
            ext = name.rsplit(".", 1)[-1].lower()
            if ext in ("shp", "shx", "dbf", "prj", "cpg"):
                if ext in members:
                    raise ValueError(f"{where}: more than one .{ext} in the zip")
                members[ext] = zf.read(name)
    missing = {"shp", "shx", "dbf", "prj"} - set(members)
    if missing:
        raise ValueError(f"{where}: zip lacks {sorted(missing)}")
    prj = members["prj"].decode("latin-1")
    if "North_American_1983" not in prj or not prj.startswith("GEOGCS"):
        raise ValueError(f"{where}: expected NAD83 geographic coordinates, .prj says {prj[:80]}")
    encoding = members.get("cpg", b"UTF-8").decode("ascii").strip() or "UTF-8"
    reader = shapefile.Reader(
        shp=io.BytesIO(members["shp"]), shx=io.BytesIO(members["shx"]), dbf=io.BytesIO(members["dbf"]), encoding=encoding
    )
    out = []
    try:
        for i, rec in enumerate(reader.iterShapeRecords()):
            shape = rec.shape
            attrs = rec.record.as_dict()
            label = f"{where} record {i} ({attrs.get('GEOID')})"
            if shape.shapeType != shapefile.POLYGON:
                raise ValueError(f"{label}: shape type {shape.shapeType}, expected polygon")
            parts = list(shape.parts) + [len(shape.points)]
            rings = [shape.points[parts[k] : parts[k + 1]] for k in range(len(parts) - 1)]
            polygons = polygons_from_shapefile_rings(rings, label)
            if not polygons:
                raise ValueError(f"{label}: no polygon survives cleaning")
            out.append((attrs, polygons))
    finally:
        reader.close()
    return out


# --- features ------------------------------------------------------------------------------------


def feature_line(fid: str, properties: dict[str, Any], polygons: list[list[Ring]]) -> str:
    props = json.dumps({"id": fid, **properties}, ensure_ascii=False, separators=(",", ":"))
    return '{"type":"Feature","id":' + json.dumps(fid) + ',"properties":' + props + ',"geometry":' + geometry_json(polygons) + "}"


def feature_collection(lines: list[str]) -> bytes:
    """One feature per line, sorted by id upstream: compact, deterministic and diff-friendly."""
    return ('{"type":"FeatureCollection","features":[\n' + ",\n".join(lines) + "\n]}\n").encode("utf-8")


def _registry() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    reg = geo.load()
    states_by_fips = {loc["fips"]: loc for loc in reg.values() if loc["level"] == "us-state"}
    return reg, states_by_fips


@dataclass
class Built:
    features: list[tuple[str, str]]  # (id, feature line)
    info: dict[str, Any]


def build_states(records, layer: Layer) -> Built:
    reg, states_by_fips = _registry()
    feats, unmatched = [], []
    for attrs, polygons in records:
        loc = states_by_fips.get(attrs["STATEFP"])
        if loc is None or loc["usps"] != attrs["STUSPS"]:
            unmatched.append(f"{attrs['GEOID']} {attrs['NAME']}")
            continue
        props = {"name": loc["name"], "usps": loc["usps"], "fips": loc["fips"], "kind": loc["kind"]}
        feats.append((loc["id"], feature_line(loc["id"], props, polygons)))
    if unmatched:
        raise ValueError(f"{layer.path}: states with no registry id: {unmatched}")
    present = {fid for fid, _ in feats}
    # The 1:20m files cover the states, DC and Puerto Rico; the 1:5m files add the Island Areas. Neither has
    # the U.S. Minor Outlying Islands (us-um).
    expected = {loc["id"] for loc in states_by_fips.values() if loc["kind"] != "territory" or loc["usps"] == "PR"}
    if layer.scale == "5m":
        expected |= {states_by_fips[fips]["id"] for fips in ISLAND_AREAS}
    missing = sorted(expected - present)
    if missing and REQUIRE_FULL_COVERAGE:
        raise ValueError(f"{layer.path}: registry states with no boundary: {missing}")
    without = sorted({loc["id"] for loc in states_by_fips.values()} - present)
    return Built(feats, {"registry_ids_without_feature": without})


def build_counties(records, layer: Layer) -> Built:
    reg, states_by_fips = _registry()
    feats, excluded, unmatched = [], [], []
    for attrs, polygons in records:
        fid = f"us-county-{attrs['GEOID']}"
        loc = reg.get(fid)
        if loc is None:
            if attrs["STATEFP"] in ISLAND_AREAS:
                excluded.append(
                    {"geoid": attrs["GEOID"], "name": attrs["NAMELSAD"], "state": f"us-{attrs['STUSPS'].lower()}",
                     "reason": "Island Area county-equivalent; the registry (2024 Gazetteer) has no county ids for the Island Areas"}
                )
            else:
                unmatched.append(f"{attrs['GEOID']} {attrs['NAMELSAD']}, {attrs['STUSPS']}")
            continue
        if loc["level"] != "us-county" or loc["parent"] != f"us-{attrs['STUSPS'].lower()}":
            unmatched.append(f"{attrs['GEOID']} {attrs['NAMELSAD']} (registry parent {loc['parent']})")
            continue
        props = {"name": loc["name"], "fips": loc["fips"], "state": loc["parent"]}
        feats.append((fid, feature_line(fid, props, polygons)))
    if unmatched:
        raise ValueError(f"{layer.path}: counties with no registry id (not invented): {unmatched}")
    present = {fid for fid, _ in feats}
    missing = sorted(k for k, v in reg.items() if v["level"] == "us-county" and k not in present)
    if missing and REQUIRE_FULL_COVERAGE:
        raise ValueError(f"{layer.path}: registry counties with no boundary: {missing[:20]}")
    return Built(feats, {"excluded": sorted(excluded, key=lambda e: e["geoid"]), "registry_ids_without_feature": missing})


CD_ID = re.compile(r"^\d{4}$")


def build_districts(records, layer: Layer) -> Built:
    _, states_by_fips = _registry()
    feats, unmatched = [], []
    for attrs, polygons in records:
        geoid, statefp, district = attrs["GEOID"], attrs["STATEFP"], attrs[f"CD{CONGRESS}FP"]
        state = states_by_fips.get(statefp)
        if state is None or not CD_ID.match(geoid) or geoid != statefp + district or str(attrs["CDSESSN"]) != str(CONGRESS):
            unmatched.append(f"{geoid} {attrs['NAMELSAD']} (session {attrs['CDSESSN']})")
            continue
        fid = f"us-cd-{geoid}"
        props = {
            "name": f"{state['name']} {attrs['NAMELSAD']}",
            "state": state["id"],
            "fips": geoid,
            "district": district,
            "at_large": district in ("00", "98"),
            "voting": district != "98",
            "congress": CONGRESS,
        }
        feats.append((fid, feature_line(fid, props, polygons)))
    if unmatched:
        raise ValueError(f"{layer.path}: districts that do not fit the us-cd-<state FIPS><district> scheme: {unmatched}")
    return Built(feats, {})


def build_countries(body: bytes, layer: Layer) -> Built:
    reg, _ = _registry()
    doc = json.loads(body)
    crs = (doc.get("crs") or {}).get("properties", {}).get("name", "urn:ogc:def:crs:OGC:1.3:CRS84")
    if not crs.endswith("CRS84"):
        raise ValueError(f"{layer.path}: expected CRS84 coordinates, the file says {crs}")
    feats, not_in_registry = [], []
    for i, feature in enumerate(doc["features"]):
        p = feature["properties"]
        iso_eh, iso_a3, adm0 = p["ISO_A3_EH"], p["ISO_A3"], p["ADM0_A3"]
        code = (adm0 if iso_a3 == "-99" else iso_eh).lower()
        loc = reg.get(code)
        if loc is not None and loc["level"] == "country":
            fid, name, in_registry = code, loc["name"], True
        else:
            fid, name, in_registry = NON_REGISTRY_PREFIX + adm0.lower(), p["NAME_LONG"], False
            note = NE_NOTES.get(adm0, f"{p['NAME_LONG']}: no ISO 3166-1 code in the registry ({p['TYPE']})")
            not_in_registry.append({"id": fid, "name": p["NAME_LONG"], "adm0_a3": adm0, "iso_a3_eh": iso_eh, "type": p["TYPE"], "note": note})
        polygons = polygons_from_geojson(feature["geometry"], f"{layer.path} feature {i} ({p['NAME']})")
        if not polygons:
            raise ValueError(f"{layer.path}: {p['NAME']} has no polygon after cleaning")
        props = {
            "name": name,
            "registry": in_registry,
            "iso_a3": None if iso_eh == "-99" else iso_eh,
            "adm0_a3": adm0,
            "ne_name": p["NAME"],
            "ne_type": p["TYPE"],
        }
        feats.append((fid, feature_line(fid, props, polygons)))
    present = {fid for fid, _ in feats}
    without = sorted(k for k, v in reg.items() if v["level"] == "country" and k not in present)
    return Built(feats, {"not_in_registry": sorted(not_in_registry, key=lambda e: e["id"]), "registry_ids_without_feature": without})


BUILDERS: dict[str, Callable[..., Built]] = {
    "census-state": build_states,
    "census-county": build_counties,
    "census-cd": build_districts,
}


def build_layer(layer: Layer, body: bytes) -> Built:
    if layer.kind == "ne-countries":
        built = build_countries(body, layer)
    else:
        built = BUILDERS[layer.kind](read_shapefile(body, layer.path), layer)
    ids = [fid for fid, _ in built.features]
    dupes = sorted(fid for fid, n in Counter(ids).items() if n > 1)
    if dupes:
        raise ValueError(f"{layer.path}: duplicate feature ids {dupes}")
    built.features.sort(key=lambda item: item[0])
    return built


# --- index ---------------------------------------------------------------------------------------


def _source(layer: Layer) -> dict[str, Any]:
    if layer.kind == "ne-countries":
        return {
            "name": f"Natural Earth Admin 0 - Countries, 1:{layer.scale}",
            "publisher": "Natural Earth",
            "file": layer.url,
            "url": NE_TERMS,
            "license": "Public domain",
            "commercial_ok": True,
            "attribution": NE_ATTRIBUTION,
            "vintage": f"Natural Earth {NE_TAG.lstrip('v')} (natural-earth-vector tag {NE_TAG})",
        }
    scale = {"20m": "1:20,000,000", "5m": "1:5,000,000"}[layer.scale]
    return {
        "name": f"U.S. Census Bureau {CENSUS_VINTAGE} Cartographic Boundary File, {layer.level.replace('-', ' ')}, {scale}",
        "publisher": "U.S. Census Bureau",
        "file": layer.url,
        "url": CENSUS_PAGE,
        "license": "Public domain (U.S. Government work); acknowledgement of the U.S. Census Bureau required",
        "commercial_ok": True,
        "attribution": CENSUS_ATTRIBUTION,
        "vintage": f"{CENSUS_VINTAGE} (GENZ{CENSUS_VINTAGE}, posted {CENSUS_POSTED})",
    }


LEVELS = {"states": "us-state", "counties": "us-county", "congressional-districts": "us-congressional-district", "countries": "country"}
ID_FORMS = {
    "states": "us-<usps>, e.g. us-ca (registry level us-state)",
    "counties": "us-county-<5-digit FIPS>, e.g. us-county-06037 (registry level us-county)",
    "congressional-districts": "us-cd-<state FIPS><2-digit district>, e.g. us-cd-0601 (not a registry level)",
    "countries": "lowercase ISO 3166-1 alpha-3, e.g. usa (registry level country); ne-<adm0_a3> where there is no registry id",
}


def _caveats(layer: Layer) -> list[str]:
    if layer.kind == "ne-countries":
        return NE_CAVEATS
    caveats = list(CENSUS_CAVEATS)
    if layer.scale == "20m":
        caveats.append("Covers the 50 states, DC and Puerto Rico; the 1:20m files leave out the Island Areas.")
    else:
        caveats.append("Covers the 50 states, DC, Puerto Rico and the Island Areas (AS, GU, MP, VI).")
    if layer.kind == "census-county":
        caveats.append("Connecticut is divided into its nine planning regions (county-equivalents since 2022), "
                       "matching the registry; the eight historical counties are not included.")
    if layer.kind == "census-cd":
        caveats.extend(CD_CAVEATS)
    return caveats


def build(raw: dict[str, bytes]) -> tuple[dict[str, bytes], dict]:
    files: dict[str, bytes] = {}
    entries = []
    counts: dict[str, int] = {}
    for layer in LAYERS:
        built = build_layer(layer, raw[layer.resource])
        body = feature_collection([line for _, line in built.features])
        files[layer.path] = body
        counts[layer.path] = len(built.features)
        source = _source(layer)
        entries.append(
            {
                "path": layer.path,
                "level": LEVELS[layer.level],
                "ids": ID_FORMS[layer.level],
                "scale": f"1:{layer.scale}",
                "feature_count": len(built.features),
                "bytes": len(body),
                "vintage": source.pop("vintage"),
                "source": {k: source[k] for k in ("name", "publisher", "file", "url")},
                "license": source["license"],
                "commercial_ok": source["commercial_ok"],
                "attribution": source["attribution"],
                "caveats": _caveats(layer),
                **built.info,
            }
        )
    total = sum(counts.values())
    index = {
        "dataset": "boundaries",
        "title": "Map boundaries",
        "description": (
            "GeoJSON boundaries for US states, counties and congressional districts and for world countries. "
            "Every feature's id (and properties.id) is the location id the rest of the datasites family uses, "
            "so a map joins to any dataset by id."
        ),
        "feature_count": total,
        "coordinates": COORDINATES_NOTE,
        "geometry": GEOMETRY_NOTE,
        "attribution": f"{CENSUS_ATTRIBUTION}. {NE_ATTRIBUTION}",
        "layers": entries,
    }
    files["index.json"] = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files, {"rows": total, "features": counts}
