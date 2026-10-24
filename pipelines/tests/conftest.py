from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.store import LocalStore

FIXTURES = Path(__file__).parent / "fixtures"
MLO_URL = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"
GL_URL = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_gl.csv"


class FakeUpstream:
    """Serves fixture files like a web server with ETags, and counts requests."""

    def __init__(self) -> None:
        self.bodies = {
            MLO_URL: (FIXTURES / "co2_mm_mlo.csv").read_bytes(),
            GL_URL: (FIXTURES / "co2_mm_gl.csv").read_bytes(),
        }
        self.last_modified = "Fri, 09 Oct 2026 19:23:07 GMT"
        self.requests: list[tuple[str, str | None]] = []

    def etag(self, url: str) -> str:
        return f'"{hash(self.bodies[url]) & 0xFFFFFFFF:x}"'

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> Fetched:
        self.requests.append((url, etag))
        current = self.etag(url)
        if etag == current:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.bodies[url], current, self.last_modified)

    def replace(self, url: str, old: bytes, new: bytes) -> None:
        assert old in self.bodies[url], f"{old!r} not in fixture"
        self.bodies[url] = self.bodies[url].replace(old, new)


@pytest.fixture
def contract():
    return load_contract("climatedata/co2-monthly")


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "r2")


@pytest.fixture
def upstream():
    return FakeUpstream()


@pytest.fixture
def raw(upstream):
    return {"noaa-mlo": upstream.bodies[MLO_URL], "world": upstream.bodies[GL_URL]}


def at(day: int, hour: int = 14) -> datetime:
    return datetime(2026, 10, day, hour, 23, tzinfo=timezone.utc)
