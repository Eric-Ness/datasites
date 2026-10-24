"""Shared helpers for the airdata pipeline tests (no conftest.py in subfolders).

Fixtures are verbatim excerpts of the real files; each starts with '#!' lines naming the source URL and
the capture date, which fixture() strips. AirData serves zips: airdata_zip() puts an excerpt back into a
zip the way EPA ships it (one CSV inside), so the pipelines see the same container as in production.
"""

from __future__ import annotations

import dataclasses
import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from datasites_core.contracts import Contract, load_contract
from datasites_core.fetch import CollectContext, Fetched

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(
    2026, 10, 24, 14, 19, tzinfo=timezone.utc
)  # the capture date of the fixtures
DAILY_YEARS = (1980, 2000, 2025, 2026)
ANNUAL_YEARS = (1980, 2025)
AIRNOW_DAYS = ("20251215", "20260415", "20260515")
LAST_MODIFIED = "Mon, 13 Jul 2026 15:47:44 GMT"


def fixture(name: str) -> bytes:
    body = (FIXTURES / name).read_bytes()
    lines = body.split(b"\n")
    while lines and lines[0].startswith(b"#!"):
        lines.pop(0)
    return b"\n".join(lines)


def airdata_zip(
    csv_name: str, body: bytes | None = None, *, date_time=(2026, 6, 26, 7, 30, 0)
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            zipfile.ZipInfo(csv_name, date_time=date_time),
            fixture(csv_name) if body is None else body,
        )
    return buf.getvalue()


def daily_raw(years=DAILY_YEARS, days=AIRNOW_DAYS) -> dict[str, bytes]:
    raw = {f"airdata-{y}": airdata_zip(f"daily_aqi_by_county_{y}.csv") for y in years}
    raw.update({f"airnow-{d}": fixture(f"airnow_daily_{d}.dat") for d in days})
    raw["airnow-sites"] = fixture("Monitoring_Site_Locations_V2.dat")
    return raw


def annual_raw(years=ANNUAL_YEARS) -> dict[str, bytes]:
    raw = {f"airdata-{y}": airdata_zip(f"annual_aqi_by_county_{y}.csv") for y in years}
    raw["aqs-sites"] = airdata_zip("aqs_sites.csv")
    return raw


def rows(table, location: str) -> dict[str, dict]:
    return {
        r["date"]: r
        for r in table.filter(table["location"] == location).iter_rows(named=True)
    }


def small(contract: Contract) -> Contract:
    """The real contract with the row-count floor lowered to fit the fixtures."""
    return dataclasses.replace(
        contract, checks=dataclasses.replace(contract.checks, min_rows=1)
    )


def daily_contract() -> Contract:
    return small(load_contract("airdata/county-aqi-daily"))


def annual_contract() -> Contract:
    return small(load_contract("airdata/county-aqi-annual"))


class Upstream:
    """Serves bodies by URL like a web server with ETags, and counts requests."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = dict(bodies)
        self.requests: list[tuple[str, str | None]] = []

    def etag(self, url: str) -> str:
        return f'"{hash(self.bodies[url]) & 0xFFFFFFFF:x}"'

    def __call__(
        self, url: str, etag: str | None, last_modified: str | None
    ) -> Fetched:
        self.requests.append((url, etag))
        if url not in self.bodies:
            raise AssertionError(f"unexpected request for {url}")
        current = self.etag(url)
        if etag == current:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.bodies[url], current, LAST_MODIFIED)


class FakeGet:
    """Stands in for CollectContext.get: the AirData page and the AirNow bucket listings, from fixtures."""

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []
        self.folders: dict[str, bytes] = {}  # day -> a replacement folder listing

    def install(self, monkeypatch) -> "FakeGet":
        monkeypatch.setattr(
            CollectContext, "get", lambda _ctx, url, **kwargs: self(url, **kwargs)
        )
        return self

    def __call__(self, url: str, *, params=None, headers=None, timeout=120) -> bytes:
        self.calls.append((url, params))
        if url.endswith("download_files.html"):
            return fixture("download_files.html")
        prefix = (params or {}).get("prefix", "")
        if "files.airnowtech.org" in url and re.fullmatch(r"airnow/\d{4}/", prefix):
            return fixture(f"airnow_listing_{prefix[7:11]}.xml")
        if "files.airnowtech.org" in url and re.fullmatch(
            r"airnow/\d{4}/\d{8}/", prefix
        ):
            return self.folders.get(prefix[12:20]) or fixture(
                f"airnow_folder_{prefix[12:20]}.xml"
            )
        raise AssertionError(f"unexpected ctx.get({url}, {params})")
