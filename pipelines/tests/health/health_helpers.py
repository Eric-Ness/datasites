"""Shared helpers for the healthdata tests (no conftest.py in domain folders).

Fixtures in fixtures/ are verbatim excerpts of the real source files, captured 2026-11-06, each starting with
'# ' comment lines that name the URL. SAHIE ships each year as a zip; `sahie()` packs an excerpt back into one.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def text(name: str, encoding: str = "utf-8") -> str:
    """A fixture without its '# ' provenance header (line endings kept as captured)."""
    lines = (FIXTURES / name).read_bytes().decode(encoding).splitlines(keepends=True)
    while lines and lines[0].startswith("# "):
        lines.pop(0)
    return "".join(lines)


def body(name: str) -> bytes:
    return text(name).encode("utf-8")


def sahie(year: int, *, edit=lambda t: t) -> bytes:
    """sahie-{year}-csv.zip holding the excerpt as sahie_{year}.csv (optionally edited first)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"sahie_{year}.csv", edit(text(f"sahie_{year}_excerpt.csv", "latin-1")).encode("latin-1"))
    return out.getvalue()
