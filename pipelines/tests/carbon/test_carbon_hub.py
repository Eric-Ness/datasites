import pytest
from carbon_helpers import HUB_XLSX, xlsx

from datasites_carbon.factors import hub


@pytest.fixture(scope="module")
def frame():
    return hub.factors(HUB_XLSX.read_bytes())


def one(frame, **match):
    found = frame
    for column, value in match.items():
        found = found.filter(found[column] == value)
    assert len(found) == 1, f"{match}: {len(found)} rows"
    return found.row(0, named=True)


def test_release_and_sheet_counts(frame):
    assert set(frame["source_version"]) == {"2026.01"}
    counts = dict(frame.group_by("source_ref").len().iter_rows())
    assert counts == {
        "Stationary Combustion": 363,
        "Mobile Combustion CO2": 10,
        "Mobile Combustion CH4 N2O": 378,
        "Electricity": 84,  # 27 subregions + US average, 3 gases, total output only
        "Steam and Heat": 3,
        "Scope 3 Cat 4, 9 Transport": 21,
        "Scope 3 Cat 6, 7 Travel": 36,
        "Scope 3 Cat 5 Waste": 183,
        "GWPs (100-year)": 84,
    }


def test_natural_gas(frame):
    co2 = one(frame, activity="Natural Gas", gas="CO2", activity_unit="mmBtu")
    assert co2["value"] == 53.06 and co2["gwp_set"] == "none" and co2["scope"] == "1"
    assert co2["category"] == "stationary-combustion" and co2["region"] == "usa"
    assert one(frame, activity="Natural Gas", gas="CH4", activity_unit="scf")["value"] == 0.00000103


def test_biogenic_co2_is_outside_the_scopes_but_peat_is_not(frame):
    wood = one(frame, activity="Biomass Fuels - Solid / Wood and Wood Residuals", gas="CO2", activity_unit="mmBtu")
    assert wood["scope"] == "outside" and "Biogenic" in wood["notes"]
    assert one(frame, activity="Biomass Fuels - Solid / Wood and Wood Residuals", gas="CH4", activity_unit="mmBtu")["scope"] == "1"
    peat = one(frame, activity="Biomass Fuels - Solid / Peat", gas="CO2", activity_unit="mmBtu")
    assert peat["scope"] == "1" and "fossil" in peat["notes"]
    e100 = one(frame, activity="Ethanol (100%)", gas="CO2", category="mobile-combustion")
    assert e100["scope"] == "outside" and e100["activity_unit"] == "gallon"
    assert one(frame, activity="Motor Gasoline", gas="CO2", category="mobile-combustion")["scope"] == "1"


def test_electricity_keeps_total_output_only(frame):
    elec = frame.filter(frame["category"] == "electricity")
    assert set(elec["activity"]) == {"Grid electricity, eGRID total output emission rate"}
    camx = one(elec, region="CAMX", gas="CO2")
    assert camx["value"] == 183.0675168 and camx["activity_unit"] == "MWh" and camx["region_type"] == "egrid-subregion"
    assert one(elec, region="usa", gas="CO2")["value"] == 339.242904
    assert 735.8807232 not in elec["value"].to_list()  # US non-baseload CO2
    assert 4.4 not in elec["value"].to_list()  # grid gross loss percentage


def test_gwp_sets_are_labelled_not_mixed(frame):
    waste = frame.filter(frame["category"] == "waste")
    assert set(waste["gwp_set"]) == {"AR4"} and set(waste["gas"]) == {"CO2e"}
    assert one(waste, activity="Aluminum Cans / Recycled")["value"] == 60
    assert "Scope 3 Categories 5 and 12." in one(waste, activity="Aluminum Cans / Recycled")["notes"]
    gwps = frame.filter(frame["source_ref"] == "GWPs (100-year)")
    assert set(gwps["gwp_set"]) == {"AR6"} and set(gwps["activity_unit"]) == {"kg"}
    assert one(gwps, activity="Global Warming Potential (GWP) / CH4")["value"] == 27
    assert one(gwps, activity="Global Warming Potential (GWP) / CH4 - fossil")["value"] == 29.8
    assert one(gwps, activity="Global Warming Potential (GWP) / N2O")["value"] == 273
    # everything else in the Hub is a per-gas mass factor
    rest = frame.filter(~frame["source_ref"].is_in(["GWPs (100-year)", "Scope 3 Cat 5 Waste"]))
    assert set(rest["gwp_set"]) == {"none"} and set(rest["gas"]) <= {"CO2", "CH4", "N2O"}


def test_scope3_rows_name_their_categories(frame):
    freight = one(frame, activity="Rail", gas="CO2", category="freight")
    assert freight["activity_unit"] == "short ton-mile" and freight["value"] == 0.021
    assert freight["notes"] == "Scope 3 Categories 4 and 9."
    car = one(frame, activity="Passenger Car", gas="CO2", category="business-travel")
    assert car["notes"] == "Scope 3 Categories 6 and 7." and car["activity_unit"] == "vehicle-mile"


def test_model_years_are_part_of_the_activity(frame):
    row = one(frame, activity="On-Road / Passenger Cars / Gasoline / model year 2007", gas="CH4")
    assert row["activity_unit"] == "vehicle-mile"
    assert one(frame, activity="On-Road / Heavy-Duty Vehicles / Gasoline / model year ≤1980", gas="N2O")["value"] == 0.0000497


def test_another_release_is_refused():
    book = xlsx({"Read Me": [["Published by:", "Cornerstone Sustainability Data Initiative"], ["Version:", "2027.01"]]})
    with pytest.raises(ValueError, match="expected the Hub 2026 release"):
        hub.factors(book)
