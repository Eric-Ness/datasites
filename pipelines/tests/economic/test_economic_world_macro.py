import json

import polars as pl
import pytest

from datasites_core.layout import blob_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_economic import worldbank_wdi as wb
from economic_helpers import FakeUpstream, at, contract, fixture


@pytest.fixture
def raw():
    return {"countries": fixture("wb_country.json"), **{code: fixture(f"wb_{code}.json") for code in wb.INDICATORS}}


@pytest.fixture
def table(raw):
    return wb.transform(raw)


def row(table, location, year):
    found = table.filter((pl.col("location") == location) & (pl.col("date") == year))
    assert len(found) == 1, f"{location}/{year}: {len(found)} rows"
    return found.row(0, named=True)


def test_economies_are_iso_codes_and_aggregates_are_left_out(table):
    assert set(table["location"]) == {"usa", "deu", "wb-xkx", "wb-chi", "world"}
    # AFE (Africa Eastern and Southern) and EUU (European Union) are aggregates in the economy list.
    assert wb.economy_locations(fixture("wb_country.json")) == {
        "USA": "usa", "DEU": "deu", "XKX": "wb-xkx", "CHI": "wb-chi", "WLD": "world",
    }  # fmt: skip


def test_indicators_side_by_side(table):
    usa = row(table, "usa", "2024")
    assert usa["gdp_usd"] == 29298013000000.0
    assert usa["gdp_growth_pct"] == pytest.approx(2.79318715363841)
    assert usa["gdp_per_capita_usd"] == pytest.approx(86169.6641581917)
    assert usa["inflation_cpi_pct"] == pytest.approx(2.94952520485207)
    assert usa["unemployment_pct"] == 4.022
    assert usa["population"] == 340003797
    assert table.schema["population"] == pl.Int64


def test_missing_values_stay_null(table):
    assert row(table, "usa", "2025")["inflation_cpi_pct"] is None  # not yet in WDI
    assert row(table, "wb-xkx", "2024")["unemployment_pct"] is None  # no ILO modeled estimate for Kosovo
    chi = row(table, "wb-chi", "2024")
    assert chi["gdp_usd"] is None and chi["population"] == 168126


def test_years_with_no_indicator_are_dropped(raw):
    # 1960 rows exist for every economy; Kosovo's 1960 has only population, so it is kept.
    table = wb.transform(raw)
    assert row(table, "wb-xkx", "1960")["population"] == 984846
    no_population = {**raw, "SP.POP.TOTL": fixture("wb_SP.POP.TOTL.json").replace(b'"value":984846', b'"value":null')}
    assert table.height - wb.transform(no_population).height == 1


def test_contract_allows_every_location(table):
    report = validate(contract("world-macro", min_rows=None, max_null_fraction={}), table.drop("location_name"), None)
    assert report.errors == []


def test_api_error_message_is_reported():
    with pytest.raises(ValueError, match="The provided parameter value is not valid"):
        wb.read_indicator(fixture("wb_error.json"), "SP.POP.TOTL")


def test_a_result_spanning_several_pages_is_refused():
    with pytest.raises(ValueError, match="17556 rows span 3512 pages"):
        wb.read_indicator(fixture("wb_SP.POP.TOTL_paged.json"), "SP.POP.TOTL")


def test_a_new_non_iso_economy_is_caught_by_validation(raw, table):
    # If the World Bank started reporting an economy without an ISO code, it would come through as
    # wb-<code>, which the contract doesn't list: validation blocks it until someone reviews it.
    countries = json.loads(raw["countries"])
    kosovo = next(c for c in countries[1] if c["id"] == "XKX")
    countries[1].append({**kosovo, "id": "XQZ", "name": "Somewhere new"})
    assert wb.economy_locations(json.dumps(countries).encode())["XQZ"] == "wb-xqz"
    newcomer = table.head(1).with_columns(pl.lit("wb-xqz").alias("location"))
    report = validate(contract("world-macro", min_rows=None), pl.concat([table, newcomer]).drop("location_name"), None)
    assert any("locations not allowed by the contract: ['wb-xqz']" in e for e in report.errors)


def upstream():
    return FakeUpstream(
        {wb.RESOURCES["countries"]: fixture("wb_country.json")}
        | {wb.RESOURCES[code]: fixture(f"wb_{code}.json") for code in wb.INDICATORS}
    )


def test_end_to_end_publish(store):
    fake = upstream()
    result = run_dataset(contract("world-macro", min_rows=None), store, fetcher=fake, now=at(24))
    assert result.outcome == "published", result.report
    doc = get_json(store, f"sites/economicdata/datasets/world-macro/versions/{result.version}.json")[0]
    assert doc["coverage"] == {"start": "1960", "end": "2025"}
    assert doc["locations"] == {
        "deu": "Germany",
        "usa": "United States",
        "wb-chi": "Channel Islands (World Bank code CHI; Jersey and Guernsey combined)",
        "wb-xkx": "Kosovo (World Bank code XKX; no ISO 3166-1 code)",
        "world": "World",
    }
    assert doc["source"]["license"] == "CC-BY-4.0"
    assert doc["source"]["attribution"].startswith("The World Bank: World Development Indicators:")
    usa = get_json(store, blob_key(doc["files"]["annual/usa.json"]["blob"]))[0]
    assert usa["meta"]["units"]["population"] == "persons"
    assert [r["date"] for r in usa["data"]] == ["1960", "2023", "2024", "2025"]


def test_validation_blocks_a_gdp_unit_slip(store):
    # A source-side unit change (say GDP suddenly reported in thousands of dollars x 1000) shows up as
    # values beyond any economy's GDP; the range check keeps it out.
    fake = upstream()
    fake.replace(wb.RESOURCES["NY.GDP.MKTP.CD"], b'"value":29298013000000', b'"value":29298013000000000')
    result = run_dataset(contract("world-macro", min_rows=None), store, fetcher=fake, now=at(24))
    assert result.outcome == "blocked"
    assert any(e.startswith("gdp_usd: 1 values outside") for e in result.report.errors)
