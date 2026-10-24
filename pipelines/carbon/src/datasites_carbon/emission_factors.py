"""carbondata/emission-factors: one long, citable table of Scope 1, 2 and 3 emission factors.

Partitions (the `source` column) and the releases they are pinned to:
    epa-hub   GHG Emission Factors Hub 2026, Cornerstone release      Zenodo 20403776 (CC BY 4.0)
    egrid     eGRID2024 subregion, state and US rates, Cornerstone    Zenodo 18968658 (CC BY 4.0)
    useeio    Supply Chain GHG Emission Factors v1.4.0, Cornerstone   Zenodo 17202747 (CC BY 4.0)
    uk-desnz  UK conversion factors 2026, flat file                   gov.uk (OGL v3)
    ember     Yearly electricity data, grid intensity                 ember-energy.org (CC BY 4.0)

The Zenodo releases and the DESNZ year are pinned on purpose: a factor library is cited by edition,
and a new edition (Hub 2027, eGRID2025, DESNZ 2027) is added deliberately, with its own factor ids,
rather than silently replacing the one an auditor checked. Within an edition, corrections still flow
in: DESNZ republishes a corrected flat file under a new link (as in July 2026), which is found
through the GOV.UK content API on every run. Ember revises its yearly file twice a month.

Zenodo ignores conditional requests (no ETag; If-Modified-Since is answered 200), so its files are
downloaded whenever the dataset is checked; the contract's weekly check_every keeps that polite.
"""

from __future__ import annotations

import polars as pl

from .factors import common, desnz, egrid, ember, hub, useeio

ZENODO = "https://zenodo.org/api/records"
HUB_URL = f"{ZENODO}/20403776/files/Emission%20Factors%20Hub_2026_machine.xlsx/content"
EGRID_URL = f"{ZENODO}/18968658/files/egrid2024_data_metric.xlsx/content"
USEEIO_URL = f"{ZENODO}/17202747/files/SupplyChainGHGEmissionFactorsv1.4.0.xlsx/content"
EMBER_URL = "https://files.ember-energy.org/public-downloads/generation/outputs/release_generation_yearly_global.csv"
DESNZ_PAGE = "https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026"
DESNZ_CONTENT_API = "https://www.gov.uk/api/content" + DESNZ_PAGE.removeprefix("https://www.gov.uk")

BUILDERS = {
    "epa-hub": hub.factors,
    "egrid": egrid.factors,
    "uk-desnz": desnz.factors,
    "ember": ember.factors,
    "useeio": useeio.factors,
}


def desnz_flat_file_url(page: dict) -> str:
    """The current 'flat file' attachment of the DESNZ publication, from its GOV.UK content API document."""
    found = [
        a["url"]
        for a in page.get("details", {}).get("attachments", [])
        if "flat file" in (a.get("title") or "").lower() and (a.get("url") or "").endswith(".xlsx")
    ]
    if len(found) != 1:
        titles = [a.get("title") for a in page.get("details", {}).get("attachments", [])]
        raise ValueError(f"expected one DESNZ flat-file attachment, found {len(found)} among {titles}")
    return found[0]


def resources(ctx) -> dict[str, str]:
    return {
        "epa-hub": HUB_URL,
        "egrid": EGRID_URL,
        "useeio": USEEIO_URL,
        "uk-desnz": desnz_flat_file_url(ctx.get_json(DESNZ_CONTENT_API)),
        "ember": EMBER_URL,
    }


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    frames = [build(raw[name]) for name, build in BUILDERS.items()]
    return common.finish(pl.concat(frames, how="vertical"))
