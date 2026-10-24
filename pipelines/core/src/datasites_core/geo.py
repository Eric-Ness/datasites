"""The shared location registry: world, ISO 3166-1 countries, US states and equivalents, US counties.

`datasites geo build` downloads the sources and writes packages/geo/src/locations.json, which the
Workers (TypeScript, @datasites/geo) and the pipelines (this module) both read. Location ids follow
docs/PLAN.md: "world", lowercase ISO alpha-3 ("usa"), "us-<usps>" ("us-ca"), "us-county-<FIPS>".
"""

from __future__ import annotations

import io
import json
import zipfile
from functools import cache
from pathlib import Path
from typing import Any, Callable

import httpx

from .contracts import repo_root

CLDR_TAG = "49.0.0"  # pinned so the registry only changes when we choose to update it
CLDR_BASE = f"https://raw.githubusercontent.com/unicode-org/cldr-json/{CLDR_TAG}/cldr-json/"
CLDR_LICENSE = f"https://raw.githubusercontent.com/unicode-org/cldr-json/{CLDR_TAG}/LICENSE"
CENSUS_COUNTIES = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_counties_national.zip"
CENSUS_STATES = "https://www2.census.gov/geo/docs/reference/state.txt"

# CLDR maps codes ISO never assigns to countries; drop them to get the 249 officially assigned codes.
PRIVATE_USE = {"AA", "ZZ"} | {f"Q{c}" for c in "MNOPQRSTUVWXYZ"} | {f"X{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
EXCEPTIONALLY_RESERVED = {"AC", "CP", "CQ", "DG", "EA", "EU", "EZ", "FX", "IC", "SU", "TA", "UK", "UN"}
TERRITORIES = {"AS", "GU", "MP", "PR", "UM", "VI"}
LEVELS = ("world", "country", "us-state", "us-county")

SOURCES = [
    {
        "name": "U.S. Census Bureau, 2024 Gazetteer Files: counties",
        "url": CENSUS_COUNTIES,
        "license": "Public domain (U.S. Government work)",
    },
    {
        "name": "U.S. Census Bureau, state and state-equivalent FIPS codes",
        "url": CENSUS_STATES,
        "license": "Public domain (U.S. Government work)",
    },
    {
        "name": f"Unicode CLDR {CLDR_TAG}: English territory names and ISO 3166 code mappings",
        "url": f"https://github.com/unicode-org/cldr-json/tree/{CLDR_TAG}",
        "license": "Unicode License v3 (see packages/geo/LICENSE-CLDR.txt)",
    },
]

Getter = Callable[[str], bytes]


def _get(url: str) -> bytes:
    res = httpx.get(url, timeout=120, follow_redirects=True)
    res.raise_for_status()
    return res.content


def _countries(get: Getter) -> list[dict[str, Any]]:
    mappings = json.loads(get(CLDR_BASE + "cldr-core/supplemental/codeMappings.json"))["supplemental"]["codeMappings"]
    aliases = json.loads(get(CLDR_BASE + "cldr-core/supplemental/aliases.json"))["supplemental"]["metadata"]["alias"]
    names = json.loads(get(CLDR_BASE + "cldr-localenames-full/main/en/territories.json"))
    names = names["main"]["en"]["localeDisplayNames"]["territories"]
    deprecated = set(aliases["territoryAlias"])

    countries = []
    for iso2, codes in mappings.items():
        if len(iso2) != 2 or not iso2.isalpha() or not iso2.isupper() or not codes or "_alpha3" not in codes:
            continue
        if iso2 in PRIVATE_USE or iso2 in EXCEPTIONALLY_RESERVED or iso2 in deprecated:
            continue
        iso3 = codes["_alpha3"]
        countries.append(
            {
                "id": iso3.lower(),
                "level": "country",
                "name": names[iso2],
                "iso2": iso2,
                "iso3": iso3,
                "numeric": codes.get("_numeric"),
                "parent": "world",
            }
        )
    return countries


def _states(get: Getter) -> list[dict[str, Any]]:
    rows = get(CENSUS_STATES).decode("utf-8").splitlines()
    header = rows[0].split("|")
    states = []
    for line in rows[1:]:
        if not line.strip():
            continue
        row = dict(zip(header, line.split("|")))
        usps = row["STUSAB"]
        states.append(
            {
                "id": f"us-{usps.lower()}",
                "level": "us-state",
                "name": row["STATE_NAME"],
                "usps": usps,
                "fips": row["STATE"],
                "kind": "district" if usps == "DC" else "territory" if usps in TERRITORIES else "state",
                "parent": "usa",
            }
        )
    return states


def _counties(get: Getter) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(get(CENSUS_COUNTIES))) as zf:
        text = zf.read(zf.infolist()[0]).decode("utf-8")
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    counties = []
    for line in lines[1:]:
        if not line.strip():
            continue
        row = dict(zip(header, (v.strip() for v in line.split("\t"))))
        counties.append(
            {
                "id": f"us-county-{row['GEOID']}",
                "level": "us-county",
                "name": row["NAME"],
                "fips": row["GEOID"],
                "parent": f"us-{row['USPS'].lower()}",
                "lat": float(row["INTPTLAT"]),
                "lon": float(row["INTPTLONG"]),
            }
        )
    return counties


def build(get: Getter = _get) -> dict[str, Any]:
    world = {"id": "world", "level": "world", "name": "World"}
    locations = [world, *_countries(get), *_states(get), *_counties(get)]
    locations.sort(key=lambda loc: (LEVELS.index(loc["level"]), loc["id"]))
    ids = [loc["id"] for loc in locations]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate location ids")
    known = set(ids)
    orphans = [loc["id"] for loc in locations if "parent" in loc and loc["parent"] not in known]
    if orphans:
        raise ValueError(f"locations whose parent is missing: {orphans[:5]}")
    return {"sources": SOURCES, "locations": locations}


def dumps(doc: dict[str, Any]) -> str:
    """One location per line, keys sorted: deterministic and diff-friendly."""
    line = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))  # noqa: E731
    body = ",\n".join(line(loc) for loc in doc["locations"])
    return '{"sources":' + line(doc["sources"]) + ',\n"locations":[\n' + body + "\n]}\n"


def package_dir() -> Path:
    return repo_root() / "packages" / "geo"


def locations_path() -> Path:
    return package_dir() / "src" / "locations.json"


def write(doc: dict[str, Any], license_text: str) -> None:
    locations_path().write_text(dumps(doc), encoding="utf-8", newline="\n")
    (package_dir() / "LICENSE-CLDR.txt").write_text(license_text, encoding="utf-8", newline="\n")
    load.cache_clear()


def check(doc: dict[str, Any]) -> bool:
    path = locations_path()
    return path.is_file() and path.read_text(encoding="utf-8") == dumps(doc)


def cldr_license(get: Getter = _get) -> str:
    return get(CLDR_LICENSE).decode("utf-8")


@cache
def load() -> dict[str, dict[str, Any]]:
    doc = json.loads(locations_path().read_text(encoding="utf-8"))
    return {loc["id"]: loc for loc in doc["locations"]}


def location(location_id: str) -> dict[str, Any] | None:
    return load().get(location_id)
