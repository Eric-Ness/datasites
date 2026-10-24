import pytest
from carbon_helpers import body, cells, useeio_xlsx, xlsx

from datasites_carbon.factors import ember, useeio


@pytest.fixture(scope="module")
def grid():
    return ember.factors(body("ember_yearly_global_excerpt.csv"))


def value(frame, region, year):
    found = frame.filter((frame["region"] == region) & (frame["valid_year"] == year))
    assert len(found) == 1, (region, year)
    return found.row(0, named=True)


def test_grid_intensity_in_kg_per_kwh(grid):
    usa = value(grid, "usa", "2024")
    assert usa["value"] == 0.38381  # 383.81 gCO2e/kWh in the source
    assert (usa["gas"], usa["activity_unit"], usa["scope"], usa["category"]) == ("CO2e", "kWh", "2", "electricity")
    assert value(grid, "usa", "2025")["value"] == 0.384403
    assert value(grid, "deu", "2024")["value"] == 0.339873
    assert value(grid, "tur", "2024")["value"] == 0.470788  # "Türkiye" mapped through its ISO code
    world = value(grid, "world", "2024")
    assert world["region_type"] == "world" and world["value"] == 0.473045


def test_lifecycle_basis_is_labelled(grid):
    assert set(grid["gwp_set"]) == {"mixed"}
    assert all("Lifecycle" in n and "GWP 21" in n for n in grid["notes"])
    assert set(grid["source_version"]) == {"yearly data to 2025"}


def test_rows_that_are_not_country_factors_are_left_out(grid):
    regions = set(zip(grid["region"], grid["valid_year"]))
    assert ("cri", "2024") in regions
    assert ("cri", "2025") not in regions  # -36.353 g/kWh: negative fossil generation, an estimation artefact
    assert not any(r == "lso" for r, _ in regions)  # no generation, blank intensity
    # the EU aggregate and Kosovo (XKX, not ISO 3166) are not regions here; the coal row is not a total
    assert len(grid) == 7  # cri 2024, deu, tur, usa 2023-2025, world


def test_a_changed_download_layout_fails_loudly():
    old_layout = b"Area,ISO 3 code,Year,Area type,Category,Subcategory,Variable,Unit,Value\n"
    with pytest.raises(ValueError, match="missing columns"):
        ember.factors(old_layout)


def test_unknown_iso_codes_fail_loudly():
    lines = body("ember_yearly_global_excerpt.csv").decode().splitlines()
    bad = [lines[0]] + [line.replace("Germany,DEU,", "Germany,XXG,") for line in lines[1:] if line.startswith("Germany,")]
    with pytest.raises(ValueError, match="not an ISO 3166-1 alpha-3 country"):
        ember.factors("\n".join(bad).encode())


def test_spend_factors_with_and_without_margins():
    frame = useeio.factors(useeio_xlsx())
    assert len(frame) == 5 * 2
    soy = {r["activity"]: r for r in frame.filter(frame["source_ref"].str.starts_with("NAICS 111110")).iter_rows(named=True)}
    assert soy["Soybean Farming (NAICS 111110), with margins"]["value"] == 0.82
    assert soy["Soybean Farming (NAICS 111110), without margins"]["value"] == 0.751
    row = soy["Soybean Farming (NAICS 111110), with margins"]
    assert (row["gwp_set"], row["gas"], row["scope"], row["valid_year"]) == ("AR6", "CO2e", "3", "2024")
    assert row["activity_unit"] == "USD (2024, purchaser price)" and row["source_ref"] == "NAICS 111110 (USEEIO 1111A0)"
    landfill = frame.filter(frame["source_ref"] == "NAICS 562212 (USEEIO 562212)")
    assert set(landfill["value"]) == {7.417}


def test_spend_factors_in_another_unit_are_refused():
    sheet = cells("useeio_v1.4.0_co2e_excerpt.csv")
    sheet[1] = [c.replace("2024 USD", "2022 USD") if c else c for c in sheet[1]]
    with pytest.raises(ValueError, match="expected 'All GHGs'"):
        useeio.factors(xlsx({"CO2e": sheet}))
