"""eGRID2024, Cornerstone release (EPA methodology, not EPA-official): Zenodo record 18968658.

The metric workbook ('egrid2024_data_metric.xlsx') gives every rate in kg. Used here: the annual
total output emission rates (kg per MWh of net generation) for CO2, CH4, N2O and CO2e, for each
eGRID subregion (SRL24), state (ST24) and the US (US24). These are the location-based Scope 2
factors. Combustion, fuel-specific, input and non-baseload rates are left out: none of them is an
inventory factor for purchased electricity.

The Cornerstone release states its CO2e uses IPCC AR5 GWPs (CH4 28, N2O 265), unlike the AR6 Hub.
"""

from __future__ import annotations

import polars as pl

from ..sheets import Workbook, header_row
from .common import clean, number, rows_frame

SOURCE = "egrid"
DATA_YEAR = "2024"
RELEASE = "eGRID2024 1.0.0"
SUFFIX = DATA_YEAR[2:]
GASES = {"CO2": "CO2RTA", "CH4": "CH4RTA", "N2O": "N2ORTA", "CO2e": "C2ERTA"}
LEVELS = (
    # sheet, field prefix, column holding the region code
    (f"SRL{SUFFIX}", "SR", "SUBRGN"),
    (f"ST{SUFFIX}", "ST", "PSTATABB"),
    (f"US{SUFFIX}", "US", None),
)
ACTIVITY = "Grid electricity, eGRID total output emission rate"
NOTE = "Location-based Scope 2: emissions at the power plant per MWh of net generation; no upstream or grid-loss emissions."
NOTE_CO2E = NOTE + " CO2e uses IPCC AR5 GWPs (CH4 28, N2O 265)."


def _field_units(book: Workbook, sheet: str) -> dict[str, str]:
    """Field code -> its description (the row above the codes), e.g. 'SRCO2RTA' -> '... (kg/MWh)'."""
    raw = book.raw(sheet)
    at = header_row(raw, "YEAR", sheet)
    if at == 0:
        raise ValueError(f"{sheet}: no description row above the field codes")
    return {code.strip(): desc or "" for code, desc in zip(raw.row(at), raw.row(at - 1)) if code}


def factors(body: bytes) -> pl.DataFrame:
    book = Workbook(body)
    rows: list[dict] = []
    for sheet, prefix, code_column in LEVELS:
        descriptions = _field_units(book, sheet)
        table = book.table(sheet, "YEAR")
        for gas, suffix in GASES.items():
            field = prefix + suffix
            if "(kg/MWh)" not in descriptions.get(field, ""):
                raise ValueError(f"{sheet}.{field} is not a kg/MWh total output rate: {descriptions.get(field)!r}")
        for row in table.iter_rows(named=True):
            if clean(row["YEAR"]) != DATA_YEAR:
                raise ValueError(f"{sheet}: data year {row['YEAR']!r}, expected {DATA_YEAR}")
            if code_column is None:
                region = ("usa", "country")
            elif prefix == "ST":
                region = (f"us-{clean(row[code_column]).lower()}", "us-state")
            else:
                region = (clean(row[code_column]), "egrid-subregion")
            for gas, suffix in GASES.items():
                field = prefix + suffix
                rows.append(
                    {
                        "source": SOURCE,
                        "source_version": RELEASE,
                        "edition": DATA_YEAR,
                        "source_ref": f"{sheet}.{field}",
                        "scope": "2",
                        "category": "electricity",
                        "activity": ACTIVITY,
                        "region": region[0],
                        "region_type": region[1],
                        "activity_unit": "MWh",
                        "gas": gas,
                        "value": number(row[field]),
                        "gwp_set": "AR5" if gas == "CO2e" else "none",
                        "valid_year": DATA_YEAR,
                        "notes": NOTE_CO2E if gas == "CO2e" else NOTE,
                    }
                )
    return rows_frame(rows)
