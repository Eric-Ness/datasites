"""Helpers for the satellitedata tests.

GCAT fixtures are verbatim excerpts of the real TSV files (the column header, the '# Updated' line and
selected records in file order), captured on 2026-10-24 with the URL in a two-line '# ' header.
The Space-Track fixture is the exception: it is schema-derived (see its header), because Space-Track
publishes no example response outside its login wall.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

from datasites_core.contracts import Contract, load_contract
from datasites_core.fetch import Fetched
from datasites_satellite.gcat import URLS

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def fixture(name: str) -> bytes:
    """A fixture's verbatim body, without the '# Source:' / '# Captured:' provenance header."""
    lines = (FIXTURES / name).read_bytes().split(b"\n")
    while lines and lines[0].startswith(b"# ") and not lines[0].startswith(b"# Updated"):
        lines.pop(0)
    return b"\n".join(lines)


def gcat_raw(module) -> dict[str, bytes]:
    """The raw dict the engine would hand a GCAT module's transform(), built from the fixtures."""
    return {name: fixture(f"gcat_{name}.tsv") for name in module.RESOURCES}


def gcat_bodies() -> dict[str, bytes]:
    """URL -> fixture body, for a fake fetcher."""
    return {URLS[name]: fixture(f"gcat_{name}.tsv") for name in URLS}


class FakeGCAT:
    """Serves the fixtures like planet4589.org does: ETags and 304s for unchanged files."""

    def __init__(self) -> None:
        self.bodies = gcat_bodies()
        self.requests: list[tuple[str, str | None]] = []

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> Fetched:
        self.requests.append((url, etag))
        current = f'"{hash(self.bodies[url]) & 0xFFFFFFFF:x}"'
        if etag == current:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.bodies[url], current, "Sat, 24 Oct 2026 01:15:24 GMT")


def relaxed(ref: str, **checks) -> Contract:
    """The real contract with row-count floors lowered to fixture size (other checks unchanged)."""
    contract = load_contract(ref)
    merged = dataclasses.replace(contract.checks, min_rows=1, **checks)
    return dataclasses.replace(contract, checks=merged)


def rows_by(table, key: str) -> dict:
    return {row[key]: row for row in table.iter_rows(named=True)}
