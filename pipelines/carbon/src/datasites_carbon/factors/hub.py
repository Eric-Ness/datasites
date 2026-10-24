"""GHG Emission Factors Hub 2026, Cornerstone release (EPA methodology, not EPA-official).

Read from the machine-readable workbook (Zenodo record 20403776, 'Emission Factors Hub_2026_machine.xlsx'),
which has one tidy sheet per Hub table. Every sheet is used except 'Heat Content (HHV)' (fuel
heating values, not emission factors) and 'Footnotes'.

Choices a GHG accountant would ask about:
- Electricity (Table 6): only the total output rates. Non-baseload rates are for estimating avoided
  emissions, and the Hub says not to use them for inventories; grid gross loss is a percentage, not
  an emission factor.
- Biogenic CO2: CO2 from the Hub's "Biomass Fuels" categories, 100% biodiesel and 100% ethanol is
  scope "outside" (GHG Protocol reports it separately); their CH4 and N2O stay in Scope 1. Peat is
  the exception: the Hub lists it under "Biomass Fuels - Solid", but the IPCC 2006 Guidelines treat
  peat CO2 like fossil CO2, so it stays in Scope 1.
- Waste (Table 9) is CO2e computed by EPA's WARM model with AR4 GWPs, labelled AR4.
- The GWP tables (Tables 11-12, AR6) are kept as refrigerant and process-gas factors: kg CO2e per
  kg of gas released.
"""

from __future__ import annotations

import re

import polars as pl

from ..sheets import Workbook, label_value
from .common import clean, join_activity, number, rows_frame

SOURCE = "epa-hub"
EDITION = "2026"
REGION = ("usa", "country")

UNITS = {
    "mmBtu": "mmBtu",
    "short ton": "short ton",
    "scf": "scf",
    "gallon": "gallon",
    "MWh": "MWh",
    "vehicle-mile": "vehicle-mile",
    "passenger-mile": "passenger-mile",
    "short ton-mile": "short ton-mile",
    "short ton material": "short ton",
}
BIOGENIC = "Biogenic CO2: GHG Protocol inventories report it separately, outside the scopes."
PEAT = (
    "The Hub lists peat under Biomass Fuels - Solid; the IPCC 2006 Guidelines treat peat CO2 as "
    "fossil, so it is reported in Scope 1 here."
)
BIOFUELS_100 = {"Biodiesel (100%)", "Ethanol (100%)"}
BLEND = "Refrigerant blend released: kg CO2e per kg, counting only its HFC and PFC constituents (IPCC AR6)."


def version(book: Workbook) -> str:
    found = label_value(book.raw("Read Me"), "Version:")
    if not found or not found.startswith(EDITION):
        raise ValueError(f"expected the Hub {EDITION} release, the Read Me sheet says version {found!r}")
    return found


def _unit(row: dict, numerator: str = "kg") -> str:
    if clean(row["UOM Numerator"]) != numerator:
        raise ValueError(f"Hub factor numerator {row['UOM Numerator']!r}, expected {numerator!r}")
    denominator = clean(row["UOM Denominator"])
    if denominator not in UNITS:
        raise ValueError(f"unknown Hub activity unit {denominator!r}")
    return UNITS[denominator]


def _scope3_note(category_text: str) -> str:
    numbers = re.findall(r"Category (\d+)", category_text)
    if not numbers:
        raise ValueError(f"no Scope 3 category numbers in {category_text!r}")
    return f"Scope 3 Categories {' and '.join(numbers)}."


def _gas(text: str | None) -> str:
    gas = clean(text)
    if gas not in ("CO2", "CH4", "N2O", "CO2e"):
        raise ValueError(f"unexpected Hub greenhouse gas {text!r}")
    return gas


def factors(body: bytes) -> pl.DataFrame:
    book = Workbook(body)
    release = version(book)
    rows: list[dict] = []

    def add(sheet: str, *, scope: str, category: str, activity: str, gas: str, value: str, unit: str,
            gwp_set: str = "none", region: tuple[str, str] = REGION, notes: str | None = None) -> None:
        rows.append(
            {
                "source": SOURCE,
                "source_version": release,
                "edition": EDITION,
                "source_ref": sheet,
                "scope": scope,
                "category": category,
                "activity": activity,
                "region": region[0],
                "region_type": region[1],
                "activity_unit": unit,
                "gas": gas,
                "value": number(value),
                "gwp_set": gwp_set,
                "valid_year": EDITION,
                "notes": notes,
            }
        )

    sheet = "Stationary Combustion"
    for row in book.table(sheet, "Emission Category").iter_rows(named=True):
        group, fuel, gas = clean(row["Emission Source Category"]), clean(row["Emission Source"]), _gas(row["Greenhouse Gas"])
        biomass = group.startswith("Biomass Fuels")
        biogenic = biomass and gas == "CO2" and fuel != "Peat"
        note = BIOGENIC if biogenic else PEAT if fuel == "Peat" and gas == "CO2" else None
        add(sheet, scope="outside" if biogenic else "1", category="stationary-combustion", activity=join_activity([group, fuel]),
            gas=gas, value=row["Emission Factor"], unit=_unit(row), notes=note)

    sheet = "Mobile Combustion CO2"
    for row in book.table(sheet, "Emission Category").iter_rows(named=True):
        fuel, gas = clean(row["Fuel Type"]), _gas(row["Greenhouse Gas"])
        biogenic = fuel in BIOFUELS_100 and gas == "CO2"
        add(sheet, scope="outside" if biogenic else "1", category="mobile-combustion", activity=fuel, gas=gas,
            value=row["Emission Factor"], unit=_unit(row), notes=BIOGENIC if biogenic else None)

    sheet = "Mobile Combustion CH4 N2O"
    for row in book.table(sheet, "Emission Category").iter_rows(named=True):
        parts = [clean(row["Emission Source Category"]), clean(row["Vehicle Type"]), clean(row["Fuel Type"])]
        model_year = clean(row["Model Year"])
        if model_year and model_year.lower() != "all":
            parts.append(f"model year {model_year}")
        add(sheet, scope="1", category="mobile-combustion", activity=join_activity(parts), gas=_gas(row["Greenhouse Gas"]),
            value=row["Emission Factor"], unit=_unit(row), notes=clean(row["Notes (if applicable)"]))

    sheet = "Electricity"
    for row in book.table(sheet, "Emission Category").iter_rows(named=True):
        kind = clean(row["Emission Source"])
        if kind in ("Non-Baseload", "N/A"):  # avoided-emissions rates and the grid gross loss percentage
            continue
        if kind != "Total Output":
            raise ValueError(f"unexpected Hub electricity row type {kind!r}")
        code = clean(row["eGRID Subregion Acronym"])
        region = REGION if code == "US Average" else (code, "egrid-subregion")
        add(sheet, scope="2", category="electricity", activity="Grid electricity, eGRID total output emission rate",
            gas=_gas(row["Greenhouse Gas"]), value=row["Emission Factor"], unit=_unit(row), region=region,
            notes="eGRID2024 rate (2024 generation) as republished in Hub Table 6, the same number as the egrid "
                  "source; location-based Scope 2.")

    sheet = "Steam and Heat"
    for row in book.table(sheet, "Emission Category").iter_rows(named=True):
        add(sheet, scope="2", category="heat-and-steam", activity=clean(row["Emission Source"]), gas=_gas(row["Greenhouse Gas"]),
            value=row["Emission Factor"], unit=_unit(row), notes="Assumes natural gas boilers at 80% thermal efficiency.")

    for sheet, category in (("Scope 3 Cat 4, 9 Transport", "freight"), ("Scope 3 Cat 6, 7 Travel", "business-travel")):
        for row in book.table(sheet, "Emission Category").iter_rows(named=True):
            add(sheet, scope="3", category=category, activity=clean(row["Vehicle Type"]), gas=_gas(row["Greenhouse Gas"]),
                value=row["Emission Factor"], unit=_unit(row), notes=_scope3_note(row["Emission Category"]))

    sheet = "Scope 3 Cat 5 Waste"
    for row in book.table(sheet, "Emission Category").iter_rows(named=True):
        source_note = clean(row["Notes"]) or ""
        if "AR4" not in source_note or _gas(row["Greenhouse Gas"]) != "CO2e":
            raise ValueError(f"Hub waste factors were expected to be AR4 CO2e, got {row!r}")
        add(sheet, scope="3", category="waste", activity=f"{clean(row['Emission Source Category'])} / {clean(row['Emission Source'])}",
            gas="CO2e", value=row["Emission Factor"], unit=_unit(row), gwp_set="AR4",
            notes=f"{_scope3_note(row['Emission Category'])} {source_note}")

    sheet = "GWPs (100-year)"
    for row in book.table(sheet, "Metric").iter_rows(named=True):
        metric = clean(row["Metric"])
        default = BLEND if metric == "Blended Refrigerants" else "Gas released: kg CO2e per kg (100-year GWP, IPCC AR6)."
        add(sheet, scope="1", category="refrigerants-and-process-gases",
            activity=f"{metric} / {clean(row['Chemical Formula or Industrial Designation'])}",
            gas="CO2e", value=row["Global Warming Potential"], unit="kg", gwp_set="AR6",
            notes=clean(row["Notes (if applicable)"]) or default)

    return rows_frame(rows)
