"""Supply Chain GHG Emission Factors for US Commodities v1.4.0 (USEEIO), Cornerstone release
(EPA methodology, not EPA-official): Zenodo record 17202747.

Spend-based Scope 3 factors (Categories 1 and 2): kg CO2e per 2024 US dollar of purchaser price,
for 1,016 six-digit 2017 NAICS commodities, all GHGs weighted with IPCC AR6 GWPs. Built on 2023
US GHG Inventory emissions. Spend from other years must be converted to 2024 dollars first.

Each commodity gives two factors from the 'CO2e' sheet:
- with margins: the full supply chain up to the purchaser, including the transport, wholesale and
  retail margins (the default for spend data);
- without margins: the same minus the margins, for buyers who report that transport and trade
  separately (for example in Category 4), so it is not counted twice.
The per-gas 'byGHG' sheet (kg of each gas, before GWPs) is not included.
"""

from __future__ import annotations

import polars as pl

from ..sheets import Workbook
from .common import clean, number, rows_frame

SOURCE = "useeio"
RELEASE = "v1.4.0"
EDITION = "v1.4"
DOLLAR_YEAR = "2024"
UNIT_TEXT = "kg CO2e/2024 USD, purchaser price"
VARIANTS = {
    "with margins": (
        "Supply Chain Emission Factors with Margins",
        "Spend-based Scope 3 (Categories 1 and 2): multiply by spend in 2024 US dollars at purchaser price. "
        "Includes transport, wholesale and retail margins.",
    ),
    "without margins": (
        "Supply Chain Emission Factors without Margins",
        "Spend-based Scope 3 (Categories 1 and 2) per 2024 US dollar at purchaser price, excluding the "
        "transport, wholesale and retail margins; use when those are reported separately.",
    ),
}


def factors(body: bytes) -> pl.DataFrame:
    table = Workbook(body).table("CO2e", "2017 NAICS Code")
    rows: list[dict] = []
    for row in table.iter_rows(named=True):
        code, title = clean(row["2017 NAICS Code"]), clean(row["2017 NAICS Title"])
        if clean(row["GHG"]) != "All GHGs" or clean(row["Unit"]) != UNIT_TEXT:
            raise ValueError(f"USEEIO NAICS {code}: expected 'All GHGs' in {UNIT_TEXT!r}, got {row['GHG']!r} {row['Unit']!r}")
        for variant, (column, note) in VARIANTS.items():
            rows.append(
                {
                    "source": SOURCE,
                    "source_version": RELEASE,
                    "edition": EDITION,
                    "source_ref": f"NAICS {code} (USEEIO {clean(row['Reference USEEIO Code'])})",
                    "scope": "3",
                    "category": "purchased-goods-and-services",
                    "activity": f"{title} (NAICS {code}), {variant}",
                    "region": "usa",
                    "region_type": "country",
                    "activity_unit": "USD (2024, purchaser price)",
                    "gas": "CO2e",
                    "value": number(row[column]),
                    "gwp_set": "AR6",
                    "valid_year": DOLLAR_YEAR,
                    "notes": note,
                }
            )
    return rows_frame(rows)
