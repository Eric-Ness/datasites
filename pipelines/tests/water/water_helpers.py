"""Helpers for the waterdata tests: verbatim fixtures, and fake USGS / USDM servers built from them.

Each fixture is a real response to a small, targeted query, captured on 2026-10-24 with its URL in a
two-line '# ' header. The fakes answer the pipeline's own (national) queries the way the real servers
would: by returning the fixture lines that match the request, verbatim.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from datasites_core.contracts import Contract, load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import blob_key, version_key
from datasites_core.store import get_json
from datasites_water import usgs

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)  # the capture date; the window is 2025-09-20..2026-10-24
DAILY_FIXTURES = ("2025-09-26", "2026-01-12", "2026-04-03")


def fixture(name: str) -> bytes:
    """A fixture's verbatim response body, without the '# ' provenance header."""
    lines = (FIXTURES / name).read_bytes().split(b"\n")
    while lines and lines[0].startswith(b"# "):
        lines.pop(0)
    return b"\n".join(lines)


def query(start: str = "2025-09-20", end: str = "2026-10-24", **extra) -> bytes:
    return json.dumps({"window_start": start, "window_end": end, **extra}, sort_keys=True).encode()


def usgs_raw(**query_args) -> dict[str, bytes]:
    """The raw dict a streamflow-daily collect() would return, built from the fixtures."""
    raw = {
        "query": query(**query_args),
        "time-series": fixture("usgs_time_series.csv"),
        "locations-000": fixture("usgs_locations.csv"),
    }
    for first in DAILY_FIXTURES:
        raw[f"daily-{first}"] = fixture(f"usgs_daily_{first}.csv")
    return raw


def relaxed(ref: str, **checks) -> Contract:
    """The real contract with some checks loosened, for end-to-end runs on a handful of fixture gages."""
    contract = load_contract(ref)
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, **checks))


def _rows(body: bytes) -> tuple[bytes, list[tuple[dict[str, str], bytes]]]:
    """(header line, [(parsed row, original line)]). These responses have no quoted newlines."""
    lines = [line for line in body.split(b"\n") if line.strip()]
    header = lines[0]
    names = next(csv.reader([header.decode().rstrip("\r")]))
    rows = []
    for line in lines[1:]:
        values = next(csv.reader([line.decode().rstrip("\r")]))
        rows.append((dict(zip(names, values)), line))
    return header, rows


def _serve(header: bytes, rows: list[bytes]) -> bytes:
    # Like the real API: an empty result is an empty body, not a header line.
    return b"\n".join([header, *rows]) + b"\n" if rows else b""


class FakeUSGS:
    """Stands in for CollectContext.get against api.waterdata.usgs.gov, serving the fixture rows."""

    def __init__(self, drop_locations: set[str] | None = None, repeat_daily: int = 1) -> None:
        self.requests: list[tuple[str, dict, dict]] = []
        self.drop_locations = drop_locations or set()
        self.repeat_daily = repeat_daily
        self.daily_header, self.daily = None, []
        for first in DAILY_FIXTURES:
            header, rows = _rows(fixture(f"usgs_daily_{first}.csv"))
            self.daily_header = header
            self.daily.extend(rows)

    def install(self, monkeypatch) -> "FakeUSGS":
        """Route the USGS module's HTTP requests here."""
        monkeypatch.setattr(usgs, "request", lambda url, **kwargs: httpx.Response(200, content=self(url, **kwargs)))
        return self

    def __call__(self, url, *, params=None, headers=None, retries=0, timeout=120):
        params = dict(params or {})
        self.requests.append((url, params, dict(headers or {})))
        assert params["f"] == "csv" and params["limit"] <= 50_000
        collection = url.split("/collections/", 1)[1].split("/", 1)[0]
        if collection == "time-series-metadata":
            assert params["parameter_code"] == "00060" and params["statistic_id"] == "00003"
            assert params["filter"].startswith("end >= '")
            return fixture("usgs_time_series.csv")
        if collection == "monitoring-locations":
            assert params["skipGeometry"] == "false"
            wanted = set(params["id"].split(","))
            header, rows = _rows(fixture("usgs_locations.csv"))
            keep = [line for row, line in rows if row["id"] in wanted and row["id"] not in self.drop_locations]
            return _serve(header, keep)
        if collection == "daily":
            first, last = params["time"].split("/")
            keep = [line for row, line in self.daily if first <= row["time"] <= last]
            keep = keep * self.repeat_daily
            return _serve(self.daily_header, keep)
        raise AssertionError(f"unexpected request {url}")


class FakeCtx:
    """A minimal collect()/resources() context (requests go through FakeUSGS.install)."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.requests = 0


class FakeUSDM:
    """A fetcher for the USDM resources: answers each state's URL with that state's fixture, or nothing."""

    def __init__(self, replace: tuple[bytes, bytes] | None = None) -> None:
        self.urls: list[str] = []
        self.replace = replace

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> Fetched:
        self.urls.append(url)
        aoi = parse_qs(urlsplit(url).query)["aoi"][0].lower()
        path = FIXTURES / f"usdm_counties_{aoi}.csv"
        body = fixture(path.name) if path.exists() else b""
        if self.replace:
            body = body.replace(*self.replace)
        return Fetched(url, 200, body, None, None)


def version_doc(store, contract: Contract, vid: str) -> dict:
    return get_json(store, version_key(contract.site, contract.id, vid))[0]


def published_file(store, doc: dict, path: str) -> bytes:
    """A published file of a version, following the sharded file map of big datasets."""
    files = dict(doc["files"])
    for shard in (doc.get("file_shards") or {}).values():
        files.update(json.loads(store.get(blob_key(shard))[0]))
    return store.get(blob_key(files[path]["blob"]))[0]


def csv_rows(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode())))
