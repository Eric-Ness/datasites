"""Reading publisher spreadsheets: every cell as text, so numbers keep the exact value Excel stores.

Publishers put titles, notes and blank rows above the real header, so a table is located by the
text of its header's first cell rather than by a fixed row number.
"""

from __future__ import annotations

import fastexcel
import polars as pl


class Workbook:
    """An XLSX file opened once (eGRID's is 16 MB) and read sheet by sheet."""

    def __init__(self, body: bytes):
        self._reader = fastexcel.read_excel(body)

    @property
    def sheet_names(self) -> list[str]:
        return list(self._reader.sheet_names)

    def find_sheet(self, wanted: str) -> str:
        """The workbook's name for a sheet, ignoring stray surrounding spaces ('Scope 3 Cat 4, 9 Transport ')."""
        for name in self.sheet_names:
            if name.strip() == wanted:
                return name
        raise ValueError(f"worksheet {wanted!r} not found (sheets: {self.sheet_names})")

    def raw(self, sheet: str) -> pl.DataFrame:
        """The whole sheet as text, one column per spreadsheet column."""
        return self._reader.load_sheet(self.find_sheet(sheet), header_row=None, dtypes="string").to_polars()

    def table(self, sheet: str, first_header: str) -> pl.DataFrame:
        """The rows below the header row whose first cell is `first_header`, named by that row.

        Blank header cells get their column position as a name; rows with every cell blank are dropped.
        """
        raw = self.raw(sheet)
        at = header_row(raw, first_header, sheet)
        names: list[str] = []
        for position, cell in enumerate(raw.row(at)):
            name = (cell or "").strip() or f"column_{position}"
            names.append(name if name not in names else f"{name}_{position}")
        table = raw.slice(at + 1).rename(dict(zip(raw.columns, names)))
        return table.filter(~pl.all_horizontal(pl.all().is_null()))


def header_row(raw: pl.DataFrame, first_header: str, sheet: str = "") -> int:
    first = raw.columns[0]
    hits = raw.with_row_index("_row").filter(pl.col(first).str.strip_chars() == first_header)["_row"]
    if hits.is_empty():
        raise ValueError(f"worksheet {sheet!r}: no header row starting with {first_header!r}")
    return int(hits[0])


def label_value(raw: pl.DataFrame, label: str) -> str | None:
    """On a cover sheet laid out as label/value pairs, the first non-blank cell to the right of `label`."""
    for row in raw.iter_rows():
        cells = [(c or "").strip() for c in row]
        for i, cell in enumerate(cells):
            if cell == label:
                rest = [c for c in cells[i + 1 :] if c]
                if rest:
                    return rest[0]
    return None
