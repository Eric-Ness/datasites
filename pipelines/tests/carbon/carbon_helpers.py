"""Shared helpers for the carbondata tests (no conftest.py in domain folders).

Fixtures in fixtures/ are verbatim excerpts of the real source files, captured 2026-10-24. Text
fixtures start with '# ' comment lines naming the URL and capture date; the binary one is described here:

    epa_hub_2026_machine.xlsx  the whole 'Emission Factors Hub_2026_machine.xlsx' (88,953 bytes, md5
                               5b182fd5288eee05a0102bbfb5fb58e5), from
                               https://zenodo.org/api/records/20403776/files/Emission%20Factors%20Hub_2026_machine.xlsx/content

The eGRID, DESNZ and USEEIO workbooks are too big to keep, so their fixtures are CSV excerpts of
worksheet cells, and `xlsx()` packs those cells back into a minimal workbook so the tests read them
through the same XLSX code path as production.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from datasites_core.fetch import Fetched
from sheet_helpers import xlsx  # noqa: F401  (shared with the housing tests)

FIXTURES = Path(__file__).parent / "fixtures"
HUB_XLSX = FIXTURES / "epa_hub_2026_machine.xlsx"


def text(name: str) -> str:
    """A text fixture without its '# ' provenance header."""
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines(keepends=True)
    while lines and lines[0].startswith("# "):
        lines.pop(0)
    return "".join(lines)


def body(name: str) -> bytes:
    return text(name).encode("utf-8")


def json_fixture(name: str) -> dict:
    return json.loads(text(name))


def cells(name: str) -> list[list[str | None]]:
    """A worksheet excerpt as rows of cells; empty cells are None, as in the spreadsheet."""
    return [[cell if cell != "" else None for cell in row] for row in csv.reader(io.StringIO(text(name)))]


def egrid_xlsx() -> bytes:
    return xlsx({sheet: cells(f"egrid2024_{sheet}_excerpt.csv") for sheet in ("SRL24", "ST24", "US24")})


def desnz_xlsx() -> bytes:
    return xlsx({"Front page": cells("desnz_2026_front_page.csv"), "Factors by Category": cells("desnz_2026_flat_excerpt.csv")})


def useeio_xlsx() -> bytes:
    return xlsx({"CO2e": cells("useeio_v1.4.0_co2e_excerpt.csv")})


def emission_factor_raw() -> dict[str, bytes]:
    """Raw bodies for every emission-factors resource, keyed like the pipeline's resources()."""
    return {
        "epa-hub": HUB_XLSX.read_bytes(),
        "egrid": egrid_xlsx(),
        "uk-desnz": desnz_xlsx(),
        "ember": body("ember_yearly_global_excerpt.csv"),
        "useeio": useeio_xlsx(),
    }


class Upstream:
    """Serves fixture bodies by URL like a web server with ETags, and counts requests."""

    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = dict(bodies)
        self.requests: list[str] = []

    def __call__(self, url: str, etag: str | None, last_modified: str | None) -> Fetched:
        self.requests.append(url)
        current = f'"{hash(self.bodies[url]) & 0xFFFFFFFF:x}"'
        if etag == current:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.bodies[url], current, "Mon, 19 Oct 2026 20:26:55 GMT")


def fake_api(documents: dict[str, dict]):
    """A stand-in for CollectContext.get_json that answers from fixture documents."""

    def get_json(self, url: str, **_kwargs):
        if url not in documents:
            raise AssertionError(f"unexpected API request {url}")
        return documents[url]

    return get_json
