"""Shared helpers for the climatedata pipeline tests (no conftest.py in subfolders).

Fixtures are verbatim excerpts of the real files; each starts with '#!' lines naming the source URL and
the capture date, which fixture() strips so the pipelines see exactly the source bytes.
"""

from __future__ import annotations

import dataclasses
import types
from pathlib import Path
from urllib.parse import urlencode

from datasites_core.contracts import Contract, load_contract
from datasites_core.fetch import Fetched

FIXTURES = Path(__file__).parent / "fixtures"
PROCDATE = "20261006"
LAST_MODIFIED = "Thu, 08 Oct 2026 14:48:28 GMT"


def fixture(name: str) -> bytes:
    body = (FIXTURES / name).read_bytes()
    lines = body.split(b"\n")
    while lines and lines[0].startswith(b"#!"):
        lines.pop(0)
    return b"\n".join(lines)


def nclimdiv_raw(level: str) -> dict[str, bytes]:
    from datasites_climate import nclimdiv

    raw = {}
    for series, (element, _code) in nclimdiv.ELEMENTS.items():
        raw[series] = fixture(f"climdiv-{element}{level}.txt")
        raw[f"{series}-normals"] = fixture(f"climdiv-norm-{element}{level}.txt")
    return raw


def nclimdiv_bodies(level: str) -> dict[str, bytes]:
    """URL -> body, as the real folder serves them for PROCDATE."""
    from datasites_climate import nclimdiv

    raw = nclimdiv_raw(level)
    return {url: raw[name] for name, url in nclimdiv.file_urls(PROCDATE, level).items()}


def global_raw() -> dict[str, bytes]:
    from datasites_climate import global_temperature as gt

    raw = {}
    for loc, (gistemp, hadcrut) in gt.REGIONS.items():
        raw[f"gistemp-{loc}"] = fixture(f"gistemp_{gistemp}.csv")
        raw[f"hadcrut5-{loc}"] = fixture(f"hadcrut5_{hadcrut}.csv")
    return raw


def ghg_raw() -> dict[str, bytes]:
    return {"ch4": fixture("ch4_mm_gl.csv"), "n2o": fixture("n2o_mm_gl.csv")}


POWER_FIXTURES = {"fra": "power_fra.json", "zaf": "power_zaf.json"}


def capitals_raw() -> dict[str, bytes]:
    raw = {"places": fixture("ne_110m_populated_places.geojson.txt")}
    for iso, name in POWER_FIXTURES.items():
        raw[f"power-{iso}"] = fixture(name)
    return raw


def relaxed(ref: str, **checks) -> Contract:
    """The real contract with row-count floors lifted, for end-to-end runs on small fixtures."""
    contract = load_contract(ref)
    changes = {"min_rows": None, **checks}
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, **changes))


class Upstream:
    """Serves bodies by URL like a web server with ETags and counts requests (a run_dataset fetcher)."""

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


class FakeGet:
    """Stands in for CollectContext.get: answers by URL (plus sorted query string) from a dict."""

    def __init__(self, answers: dict[str, bytes]):
        self.answers = answers
        self.calls: list[str] = []

    def __get__(self, ctx, owner=None):
        """Installed with monkeypatch.setattr(CollectContext, "get", fake), it binds like a method."""
        return self if ctx is None else types.MethodType(self, ctx)

    @staticmethod
    def key(url: str, params: dict | None = None) -> str:
        return f"{url}?{urlencode(sorted((k, str(v)) for k, v in params.items()))}" if params else url

    def __call__(self, ctx, url: str, *, params=None, headers=None, timeout: float = 120) -> bytes:
        key = self.key(url, params)
        self.calls.append(key)
        ctx.requests += 1
        if key not in self.answers:
            raise AssertionError(f"unexpected request {key}")
        return self.answers[key]
