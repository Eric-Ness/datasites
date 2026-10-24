"""Shared helpers for the economicdata tests (pytest forbids a second conftest.py in subfolders)."""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from datasites_core.contracts import Contract, load_contract
from datasites_core.fetch import Fetched

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    """A fixture's bytes without its leading '#' provenance header."""
    lines = (FIXTURES / name).read_bytes().split(b"\n")
    while lines and lines[0].startswith(b"#"):
        lines.pop(0)
    return b"\n".join(lines)


def contract(dataset: str, **checks) -> Contract:
    """The real contract, optionally with some checks overridden (e.g. min_rows=None for tiny fixtures)."""
    c = load_contract(f"economicdata/{dataset}")
    return dataclasses.replace(c, checks=dataclasses.replace(c.checks, **checks)) if checks else c


def at(day: int, month: int = 10, hour: int = 14) -> datetime:
    return datetime(2026, month, day, hour, 31, tzinfo=timezone.utc)


class FakeUpstream:
    """Serves bodies by URL like a web server with ETags, and records requests."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = dict(bodies)
        self.requests: list[tuple[str, str | None]] = []

    def etag(self, url: str) -> str:
        return '"' + hashlib.sha256(self.bodies[url]).hexdigest()[:16] + '"'

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> Fetched:
        self.requests.append((url, etag))
        if url not in self.bodies:
            raise AssertionError(f"unexpected request for {url}")
        current = self.etag(url)
        if etag == current:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.bodies[url], current, "Tue, 20 Oct 2026 14:00:00 GMT")

    def replace(self, url: str, old: bytes, new: bytes) -> None:
        assert old in self.bodies[url], f"{old!r} not in {url}"
        self.bodies[url] = self.bodies[url].replace(old, new)


class FakeContext:
    """Stands in for datasites_core.fetch.CollectContext: canned responses, a fake key, a fixed clock."""

    def __init__(self, contract: Contract, responses, env: dict[str, str] | None = None, now: datetime | None = None):
        self.contract = contract
        self.responses = responses  # callable(url, params) -> bytes, may raise
        self.environ = env or {}
        self.now = now or at(24)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, *, params=None, headers=None, timeout: float = 120) -> bytes:
        self.calls.append((url, dict(params or {})))
        return self.responses(url, params or {})

    def env(self, name: str) -> str:
        if name not in self.environ:
            raise RuntimeError(f"{self.contract.ref} needs the environment variable {name}")
        return self.environ[name]
