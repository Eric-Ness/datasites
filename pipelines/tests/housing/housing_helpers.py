"""Shared helpers for the housingdata tests (no conftest.py in domain folders).

Fixtures in fixtures/ are verbatim excerpts of the real source files, captured 2026-11-06, each starting with
'# ' comment lines that name the URL. FHFA's workbooks are kept as CSV excerpts of their cells and packed back
into XLSX by sheet_helpers.xlsx(), so the tests read them through the production code path.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from sheet_helpers import xlsx

FIXTURES = Path(__file__).parent / "fixtures"


def text(name: str, encoding: str = "utf-8") -> str:
    """A fixture without its '# ' provenance header."""
    lines = (FIXTURES / name).read_text(encoding=encoding).splitlines(keepends=True)
    while lines and lines[0].startswith("# "):
        lines.pop(0)
    return "".join(lines)


def bps(year: int) -> bytes:
    """A Building Permits Survey excerpt, as the bytes of co{year}a.txt (Latin-1, like the real files)."""
    return text(f"bps_co{year}a_excerpt.txt", "latin-1").encode("latin-1")


def cells(name: str) -> list[list[str | None]]:
    """A worksheet excerpt as rows of cells; empty cells are None, as in the workbook."""
    return [[cell if cell != "" else None for cell in row] for row in csv.reader(io.StringIO(text(name)))]


def fhfa(level: str) -> bytes:
    """An FHFA annual HPI workbook ("county" or "state") holding the excerpt's cells."""
    return xlsx({level: cells(f"fhfa_hpi_at_{level}_excerpt.csv")})
