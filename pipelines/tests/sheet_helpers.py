"""Builds minimal XLSX workbooks from worksheet cells, for tests of pipelines that read spreadsheets.

Fixtures keep verbatim cell excerpts as CSV (a whole real workbook is usually too big to commit); `xlsx()`
packs them back into a workbook so the tests go through the same XLSX code path as production. pytest
puts tests/ on the path (it holds the root conftest.py), so any domain's helpers can import this.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


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
