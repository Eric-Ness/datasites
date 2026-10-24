"""UK Government GHG conversion factors for company reporting 2026 (DESNZ), flat file.

The flat file lists every factor of the full set as one row with a stable ID. DESNZ publishes each
activity's total (kg CO2e) and, for many activities, its split by gas (kg CO2e of CO2, CH4, N2O);
the split rows sum to the total, so they are labelled with their gas and must not be added to it.
All CO2e uses IPCC AR5 GWPs (refrigerants: mostly AR5, with AR4, AR6 or EU F-gas values where AR5
has none). DESNZ says the 2026 factors are for activity in calendar year 2026.

Left out: blank cells (DESNZ marks factors that are not available by leaving them empty) and the
"SECR kWh" sections, which convert distance to energy (kWh per km) rather than to emissions.
Region is the UK (gbr) except hotel stays, which are factors for the country of the hotel.
"""

from __future__ import annotations

import re

import polars as pl

from ..sheets import Workbook, label_value
from .common import clean, country_from_name, join_activity, number, rows_frame

SOURCE = "uk-desnz"
EDITION = "2026"
SHEET = "Factors by Category"

SCOPES = {"Scope 1": "1", "Scope 2": "2", "Scope 3": "3", "Outside of Scopes": "outside"}
GASES = {
    "kg CO2e": "CO2e",
    "kg CO2e of CO2 per unit": "CO2",
    "kg CO2e of CH4 per unit": "CH4",
    "kg CO2e of N2O per unit": "N2O",
}
UNITS = {
    "kWh": "kWh",
    "kWh (Net CV)": "kWh (net CV)",
    "kWh (Gross CV)": "kWh (gross CV)",
    "GJ": "GJ",
    "tonnes": "tonne",
    "kg": "kg",
    "litres": "litre",
    "million litres": "million litres",
    "cubic metres": "m3",
    "km": "vehicle-km",  # DESNZ "km" and "miles" are distance driven by the vehicle (cars, vans, HGVs, taxis)
    "miles": "vehicle-mile",
    "passenger.km": "passenger-km",
    "tonne.km": "tonne-km",
    "Room per night": "room-night",
    "per FTE Working Hour": "FTE working hour",
}
CATEGORIES = {
    "Fuels": "fuels",
    "Bioenergy": "fuels",
    "Refrigerant & other": "refrigerants-and-process-gases",
    "Passenger vehicles": "mobile-combustion",
    "Delivery vehicles": "mobile-combustion",
    "UK electricity": "electricity",
    "UK electricity for EVs": "electricity",
    "Heat and steam": "heat-and-steam",
    "WTT- fuels": "well-to-tank",
    "WTT- bioenergy": "well-to-tank",
    "WTT- UK electricity": "well-to-tank",
    "WTT- heat and steam": "well-to-tank",
    "WTT- business travel- air": "well-to-tank",
    "WTT- business travel- sea": "well-to-tank",
    "WTT- pass vehs & travel- land": "well-to-tank",
    "WTT- delivery vehs & freight": "well-to-tank",
    "Transmission and distribution": "transmission-and-distribution",
    "UK electricity T&D for EVs": "transmission-and-distribution",
    "Water supply": "water",
    "Water treatment": "water",
    "Material use": "materials",
    "Waste disposal": "waste",
    "Business travel- air": "business-travel",
    "Business travel- sea": "business-travel",
    "Business travel- land": "business-travel",
    "Hotel stay": "business-travel",
    "Freighting goods": "freight",
    "Managed assets- electricity": "electricity",
    "Managed assets- vehicles": "mobile-combustion",
    "Homeworking": "homeworking",
}
# "Outside of scopes" holds biogenic CO2; its category follows what was burned.
OUTSIDE_CATEGORIES = {
    "Biofuel": "fuels",
    "Biomass": "fuels",
    "Biogas": "fuels",
    "Forecourt fuels containing biofuel": "fuels",
    "Electricity generated": "electricity",
}
# Hotel-stay country names that differ from the geo registry's English names.
COUNTRY_ALIASES = {
    "UK": "gbr",
    "UK (London)": "gbr",
    "Czech Republic": "cze",
    "Hong Kong, China": "hkg",
    "Macau, China": "mac",
    "Taiwan, China": "twn",
    "Korea": "kor",
    "Russian Federation": "rus",
    "Turkey": "tur",
}
NOTES = {
    "outside": "Biogenic CO2: GHG Protocol inventories report it separately, outside the scopes.",
    "refrigerant": "DESNZ GWPs are mainly IPCC AR5, with AR4, AR6 or EU F-gas values where AR5 has none.",
    "non-kyoto": (
        "Includes non-Kyoto (Montreal Protocol) gases, which inventories report separately from the "
        "scopes; use the 'only Kyoto products' factor for Scope 1."
    ),
    "rf": "Includes DESNZ's uplift for the non-CO2 radiative forcing (RF) effects of aviation.",
}


def version(book: Workbook) -> str:
    front = book.raw("Front page")
    year, number_ = label_value(front, "Year:"), label_value(front, "Version:")
    if year != EDITION or not number_:
        raise ValueError(f"expected the {EDITION} flat file, the front page says year {year!r}, version {number_!r}")
    return f"{EDITION} v{number_}"


def factors(body: bytes) -> pl.DataFrame:
    book = Workbook(body)
    release = version(book)
    table = book.table(SHEET, "ID")
    value_columns = [c for c in table.columns if re.fullmatch(r"GHG Conversion Factor \d{4}", c)]
    if value_columns != [f"GHG Conversion Factor {EDITION}"]:
        raise ValueError(f"expected one 'GHG Conversion Factor {EDITION}' column, found {value_columns}")
    value_column = value_columns[0]

    rows: list[dict] = []
    for row in table.iter_rows(named=True):
        factor_id = clean(row["ID"])
        if factor_id is None:  # the END marker row
            continue
        ghg_unit = clean(row["GHG/Unit"])
        if ghg_unit and ghg_unit.startswith("kWh"):
            continue  # SECR energy conversion, not an emission factor
        if ghg_unit not in GASES:
            raise ValueError(f"DESNZ {factor_id}: unexpected GHG/Unit {ghg_unit!r}")
        value = number(row[value_column])
        if value is None:
            continue  # DESNZ leaves factors that are not available blank

        level1, level2, level3 = clean(row["Level 1"]), clean(row["Level 2"]), clean(row["Level 3"])
        column_text = clean(row["Column Text"])
        scope = SCOPES.get(clean(row["Scope"]) or "")
        category = OUTSIDE_CATEGORIES.get(level2) if scope == "outside" else CATEGORIES.get(level1)
        unit = UNITS.get(clean(row["UOM"]) or "")
        if scope is None or category is None or unit is None:
            raise ValueError(f"DESNZ {factor_id}: unmapped scope/category/unit {row!r}")

        region = country_from_name(level3, COUNTRY_ALIASES) if level1 == "Hotel stay" else "gbr"
        notes = []
        if scope == "outside":
            notes.append(NOTES["outside"])
        if level1 == "Refrigerant & other":
            notes.append(NOTES["refrigerant"])
            if column_text and "non-Kyoto" in column_text:
                notes.append(NOTES["non-kyoto"])
        if column_text == "With RF":
            notes.append(NOTES["rf"])

        parts = [level1, level2, level3, clean(row["Level 4"]), column_text]
        rows.append(
            {
                "source": SOURCE,
                "source_version": release,
                "edition": EDITION,
                "source_ref": factor_id,
                "scope": scope,
                "category": category,
                "activity": join_activity(parts),
                "region": region,
                "region_type": "country",
                "activity_unit": unit,
                "gas": GASES[ghg_unit],
                "value": value,
                "gwp_set": "AR5",
                "valid_year": EDITION,
                "notes": " ".join(notes) or None,
            }
        )
    return rows_frame(rows)
