import pytest
from carbon_helpers import cells, desnz_xlsx, xlsx

from datasites_carbon.factors import desnz


@pytest.fixture(scope="module")
def frame():
    return desnz.factors(desnz_xlsx())


def by_ref(frame, ref):
    found = frame.filter(frame["source_ref"] == ref)
    assert len(found) == 1, ref
    return found.row(0, named=True)


def test_release_and_what_is_left_out(frame):
    assert set(frame["source_version"]) == {"2026 v1.2"}
    refs = set(frame["source_ref"])
    # blank cells: SF6 "only non-Kyoto", CFC-11 (Montreal), PHEV Mini on UK electricity
    assert {"3_200_2076_3_1", "3_202_2351_3_1", "8_300_3003_4_1"}.isdisjoint(refs)
    # SECR kWh rows convert distance to energy; they are not emission factors
    assert "6_300_3000_4_5" not in refs
    # 33 factor rows in the excerpt - 3 blank - 1 SECR
    assert len(frame) == 29


def test_gas_split_rows_are_labelled_and_sum_to_the_total(frame):
    total = by_ref(frame, "1_100_1000_15_1")
    parts = [by_ref(frame, f"1_100_1000_15_{i}") for i in (2, 3, 4)]
    assert total["gas"] == "CO2e" and [p["gas"] for p in parts] == ["CO2", "CH4", "N2O"]
    assert {r["activity_unit"] for r in [total, *parts]} == {"tonne"}
    assert {r["gwp_set"] for r in [total, *parts]} == {"AR5"}
    assert sum(p["value"] for p in parts) == pytest.approx(total["value"], abs=1e-4)
    assert total["value"] == 3033.38067 and total["activity"] == "Fuels / Gaseous fuels / Butane"


def test_units_keep_the_heating_value_basis(frame):
    assert by_ref(frame, "1_100_1004_6_1")["activity_unit"] == "kWh (gross CV)"
    assert by_ref(frame, "1_100_1004_7_1")["activity_unit"] == "kWh (net CV)"
    assert by_ref(frame, "1_100_1004_6_1")["value"] == 0.18231
    assert by_ref(frame, "17_404_4005_1_1")["activity_unit"] == "m3"
    assert by_ref(frame, "17_404_4005_10_1")["activity_unit"] == "million litres"
    assert by_ref(frame, "25_313_3141_4_1")["activity_unit"] == "vehicle-km"
    assert by_ref(frame, "21_316_3170_11_1")["activity_unit"] == "passenger-km"
    assert by_ref(frame, "27_315_3151_14_1")["activity_unit"] == "tonne-km"
    assert by_ref(frame, "29_600_4000_13_1")["activity_unit"] == "room-night"
    assert by_ref(frame, "32_603_9999_12_1")["activity_unit"] == "FTE working hour"


def test_scopes_and_categories(frame):
    uk = by_ref(frame, "7_400_4000_5_1")
    assert (uk["scope"], uk["category"], uk["region"], uk["value"]) == ("2", "electricity", "gbr", 0.13096)
    assert by_ref(frame, "13_402_4000_5_1")["category"] == "transmission-and-distribution"
    assert by_ref(frame, "15_917_4000_5_1")["category"] == "well-to-tank"
    assert by_ref(frame, "11_100_1000_15_1")["scope"] == "3"
    assert by_ref(frame, "31_307_3000_4_1")["scope"] == "3"  # managed (leased) vehicles
    biogenic = by_ref(frame, "99_400_4001_5_2")
    assert (biogenic["scope"], biogenic["category"], biogenic["gas"]) == ("outside", "electricity", "CO2")
    assert "outside the scopes" in biogenic["notes"]
    assert by_ref(frame, "99_104_1043_15_2")["category"] == "fuels"
    # a battery car has no tailpipe emissions: a real zero, kept
    assert by_ref(frame, "4_300_3004_4_1")["value"] == 0


def test_hotel_stays_are_placed_in_the_hotel_country(frame):
    hotels = {r["activity"]: r["region"] for r in frame.filter(frame["activity"].str.starts_with("Hotel stay")).iter_rows(named=True)}
    assert hotels == {
        "Hotel stay / UK": "gbr",
        "Hotel stay / UK (London)": "gbr",
        "Hotel stay / Hong Kong, China": "hkg",
        "Hotel stay / Korea": "kor",
        "Hotel stay / Turkey": "tur",
        "Hotel stay / United States": "usa",
    }
    assert by_ref(frame, "29_600_4028_13_1")["value"] == 55.8


def test_caveat_notes(frame):
    assert "radiative forcing" in by_ref(frame, "21_316_3170_11_1")["notes"]
    assert by_ref(frame, "21_316_3171_11_1")["notes"] is None  # without RF
    sf6_total = by_ref(frame, "3_200_2077_3_1")
    assert "non-Kyoto" in sf6_total["notes"] and sf6_total["value"] == 23500
    assert "non-Kyoto" not in by_ref(frame, "3_200_2075_3_1")["notes"]


def test_unknown_layouts_fail_loudly():
    sheet = cells("desnz_2026_flat_excerpt.csv")
    renamed = [["New category" if c == "Fuels" else c for c in row] for row in sheet]
    with pytest.raises(ValueError, match="unmapped scope/category/unit"):
        desnz.factors(xlsx({"Front page": cells("desnz_2026_front_page.csv"), "Factors by Category": renamed}))
    other_year = [[c.replace("2026", "2027") if c else c for c in row] for row in cells("desnz_2026_front_page.csv")]
    with pytest.raises(ValueError, match="expected the 2026 flat file"):
        desnz.factors(xlsx({"Front page": other_year, "Factors by Category": sheet}))
