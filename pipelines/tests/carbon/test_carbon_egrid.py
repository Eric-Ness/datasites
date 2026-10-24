import pytest
from carbon_helpers import cells, egrid_xlsx, xlsx

from datasites_carbon.factors import egrid


def rows(frame, region):
    return {r["gas"]: r for r in frame.filter(frame["region"] == region).iter_rows(named=True)}


def test_subregion_rates_and_gwp_labels():
    camx = rows(egrid.factors(egrid_xlsx()), "CAMX")
    assert set(camx) == {"CO2", "CH4", "N2O", "CO2e"}
    assert camx["CO2"]["value"] == 183.0675168
    assert camx["CH4"]["value"] == 0.01134
    assert camx["N2O"]["value"] == 0.0013608
    assert camx["CO2e"]["value"] == 183.7946376
    # eGRID2024 (Cornerstone) weights CO2e with AR5; the per-gas rates are plain mass.
    assert camx["CO2e"]["gwp_set"] == "AR5"
    assert {camx[g]["gwp_set"] for g in ("CO2", "CH4", "N2O")} == {"none"}
    assert camx["CO2"]["source_ref"] == "SRL24.SRCO2RTA"
    assert camx["CO2"]["region_type"] == "egrid-subregion"
    assert camx["CO2"]["activity_unit"] == "MWh" and camx["CO2"]["scope"] == "2"
    assert camx["CO2"]["valid_year"] == "2024" and camx["CO2"]["source_version"] == "eGRID2024 1.0.0"


def test_states_and_us_use_registry_ids():
    frame = egrid.factors(egrid_xlsx())
    states = set(frame.filter(frame["region_type"] == "us-state")["region"])
    assert states == {"us-ca", "us-dc", "us-pr", "us-tx", "us-vt", "us-wv"}
    assert rows(frame, "us-wv")["CO2"]["value"] == 876.1274928
    us = rows(frame, "usa")
    assert us["CO2"]["source_ref"] == "US24.USCO2RTA" and us["CO2"]["region_type"] == "country"
    # 5 subregions + 6 states + the US, four gases each
    assert len(frame) == (5 + 6 + 1) * 4


def test_non_baseload_rates_are_not_factors():
    frame = egrid.factors(egrid_xlsx())
    # SRNBCO2 (CAMX non-baseload CO2, 530.6067648) is in the excerpt but is not an inventory factor.
    assert 530.6067648 not in frame["value"].to_list()


def test_a_pound_based_workbook_is_refused():
    sheets = {s: cells(f"egrid2024_{s}_excerpt.csv") for s in ("SRL24", "ST24", "US24")}
    sheets["SRL24"][0] = [c.replace("(kg/MWh)", "(lb/MWh)") if c else c for c in sheets["SRL24"][0]]
    with pytest.raises(ValueError, match="not a kg/MWh total output rate"):
        egrid.factors(xlsx(sheets))
