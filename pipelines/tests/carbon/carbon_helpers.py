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
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from datasites_core.fetch import Fetched

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


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def xlsx(sheets: dict[str, list[list[str | None]]]) -> bytes:
    """A minimal workbook holding every cell as an inline string."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    names = list(sheets)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(names))
        )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{pkg}">'
            f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        sheet_list = "".join(
            f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, name in enumerate(names)
        )
        zf.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>{sheet_list}</sheets></workbook>',
        )
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Type="{rel}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(names))
        )
        zf.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{pkg}">{rels}</Relationships>')
        for i, name in enumerate(names):
            rows = []
            for r, row in enumerate(sheets[name], start=1):
                cs = "".join(
                    f'<c r="{_column(c)}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
                    for c, value in enumerate(row)
                    if value is not None
                )
                rows.append(f'<row r="{r}">{cs}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{i + 1}.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}"><sheetData>{"".join(rows)}</sheetData></worksheet>',
            )
    return out.getvalue()


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
