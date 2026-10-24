"""Shared helpers for the wildfiredata pipeline tests (no conftest.py in subfolders).

Text fixtures are verbatim excerpts of the real responses; each starts with '#!' lines naming the source
URL and the capture date, which fixture() strips so the pipelines see exactly the source bytes. Zip
fixtures cannot carry a comment, so each has a '<name>.source.txt' next to it with the same header.
"""

from __future__ import annotations

import dataclasses
import types
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit


from datasites_core.contracts import Contract, load_contract
from datasites_core.fetch import Fetched

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 24, 14, 37, tzinfo=timezone.utc)
LAST_MODIFIED = "Sat, 24 Oct 2026 12:40:58 GMT"
INCIDENTS_COUNT = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
PERIMETERS_COUNT = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"


def fixture(name: str) -> bytes:
    body = (FIXTURES / name).read_bytes()
    if name.endswith(".zip"):
        assert (FIXTURES / f"{name}.source.txt").exists(), f"{name} has no provenance file"
        return body
    lines = body.split(b"\n")
    assert lines[0].startswith(b"#! Source: "), f"{name} has no source header"
    while lines and lines[0].startswith(b"#!"):
        lines.pop(0)
    return b"\n".join(lines)


def firms_raw() -> dict[str, bytes]:
    from datasites_wildfire import firms

    return {name: fixture(f"{name}.csv") for name in firms.file_urls()}


def active_fires_raw() -> dict[str, bytes]:
    return {
        **firms_raw(),
        "admin1": fixture("ne_10m_admin_1_excerpt.zip"),
        "incidents-page-000": fixture("af-incidents-page-000.json"),
        "perimeters-page-000": fixture("af-perimeters-page-000.json"),
    }


def relaxed(ref: str, **checks) -> Contract:
    """The real contract with row-count floors lifted, for end-to-end runs on small fixtures."""
    contract = load_contract(ref)
    changes = {"min_rows": None, **checks}
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, **changes))


class Upstream:
    """Serves bodies by URL like a web server with ETags (a run_dataset fetcher)."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = dict(bodies)
        self.requests: list[str] = []

    def etag(self, url: str) -> str:
        return f'"{hash(self.bodies[url]) & 0xFFFFFFFF:x}"'

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> Fetched:
        self.requests.append(url)
        if url not in self.bodies:
            raise AssertionError(f"unexpected fetch of {url}")
        current = self.etag(url)
        if etag == current:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.bodies[url], current, LAST_MODIFIED)


def canonical(url: str, params: dict | None = None) -> str:
    """URL with its query parameters merged and sorted, so a dict of answers can be keyed on it."""
    parts = urlsplit(url)
    query = parse_qsl(parts.query) + [(k, str(v)) for k, v in (params or {}).items()]
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return f"{base}?{urlencode(sorted(query))}" if query else base


class FakeGet:
    """Stands in for CollectContext.get: answers by canonical URL from a dict."""

    def __init__(self, answers: dict[str, bytes]):
        self.answers = {canonical(k): v for k, v in answers.items()}
        self.calls: list[str] = []

    def __get__(self, ctx, owner=None):
        return self if ctx is None else types.MethodType(self, ctx)

    def __call__(self, ctx, url: str, *, params=None, headers=None, timeout: float = 120) -> bytes:
        key = canonical(url, params)
        self.calls.append(key)
        ctx.requests += 1
        if key not in self.answers:
            raise AssertionError(f"unexpected request {key}")
        return self.answers[key]


def count_url(layer_query: str) -> str:
    return canonical(layer_query, {"where": "1=1", "returnCountOnly": "true", "f": "json"})


def firms_csv(rows: list[tuple]) -> bytes:
    """A FIRMS-shaped CSV body from (lat, lon, 'YYYY-MM-DD', 'HHMM', satellite, frp) tuples, for the
    clustering unit tests: these test inputs exercise geometry (dateline, poles, exact gaps) that a
    real excerpt cannot pin down, so they are constructed, and only ever used as algorithm inputs."""
    header = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight"
    lines = [header] + [f"{lat},{lon},330.0,0.4,0.4,{d},{t},{sat},nominal,2.0NRT,290.0,{frp},D" for lat, lon, d, t, sat, frp in rows]
    return "\n".join(lines).encode()
