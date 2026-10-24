"""Shared helpers for the visualizedata tests (no conftest.py in subfolders).

The fixtures are real subsets of the Census 2025 cartographic boundary files and verbatim Natural Earth
v5.1.2 features; visualize_fixture_cut.py shows how each was cut, and each zip has a .source.txt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from datasites_core.fetch import Fetched
from datasites_visualize import boundaries

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 24, 14, 53, tzinfo=timezone.utc)
LAST_MODIFIED = "Thu, 23 Apr 2026 13:45:00 GMT"


def fixture(name: str) -> bytes:
    if name.endswith(".zip"):
        assert (FIXTURES / f"{name}.source.txt").exists(), f"{name} has no provenance file"
    else:
        assert b'"_fixture": "Verbatim features' in (FIXTURES / name).read_bytes()[:400], f"{name} has no provenance member"
    return (FIXTURES / name).read_bytes()


def fixture_raw() -> dict[str, bytes]:
    """The pipeline's raw inputs, each from the fixture subset of the same source file."""
    return {layer.resource: fixture(layer.url.rsplit("/", 1)[1]) for layer in boundaries.LAYERS}


def fetcher():
    by_url = {layer.url: fixture(layer.url.rsplit("/", 1)[1]) for layer in boundaries.LAYERS}

    def fetch(url: str, etag: str | None, last_modified: str | None) -> Fetched:
        body = by_url[url]
        tag = f'"{len(body):x}"'
        if etag == tag:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, body, tag, LAST_MODIFIED)

    return fetch


def features(body: bytes) -> list[dict[str, Any]]:
    doc = json.loads(body)
    assert doc["type"] == "FeatureCollection"
    return doc["features"]


def by_id(body: bytes) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in features(body)}


def polygons(feature: dict[str, Any]) -> list[list[list[list[float]]]]:
    geom = feature["geometry"]
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def rings(feature: dict[str, Any]) -> Iterator[tuple[bool, list[list[float]]]]:
    """(is_exterior, ring) for every ring of a feature."""
    for poly in polygons(feature):
        for k, ring in enumerate(poly):
            yield k == 0, ring
