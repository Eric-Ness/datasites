import dataclasses
import json
from datetime import datetime, timezone

import polars as pl
import pytest
from carbon_helpers import Upstream, cells, emission_factor_raw, fake_api, json_fixture, xlsx

from datasites_carbon import emission_factors as ef
from datasites_carbon.factors import common
from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.layout import blob_key, manifest_key
from datasites_core.store import get_json

NOW = datetime(2026, 10, 24, 6, 41, tzinfo=timezone.utc)
FLAT_FILE = "https://assets.publishing.service.gov.uk/media/6a6c9748862aaf18d9c62ac9/ghg-conversion-factors-2026-flat-format-revised.xlsx"


@pytest.fixture(scope="module")
def table():
    return ef.transform(emission_factor_raw())


def test_table_matches_the_contract(table):
    contract = load_contract("carbondata/emission-factors")
    assert table.columns == contract.columns == common.COLUMNS
    assert table["factor_id"].is_duplicated().sum() == 0
    assert set(table["source"]) == set(common.SOURCES)
    assert table["factor_id"].str.contains(r"^[a-z0-9-]+:[a-z0-9.-]+:[a-z-]+:[a-z0-9-]+:[a-z0-9-]+:(co2|ch4|n2o|co2e):[a-z0-9-]+$").all()


def test_ids_and_units_read_naturally(table):
    def row(factor_id):
        found = table.filter(table["factor_id"] == factor_id)
        assert len(found) == 1, factor_id
        return found.row(0, named=True)

    gas = row("epa-hub:2026:stationary-combustion:natural-gas:usa:co2:mmbtu")
    assert (gas["value"], gas["unit"]) == (53.06, "kg CO2/mmBtu")
    butane = row("uk-desnz:2026:fuels:fuels-gaseous-fuels-butane:gbr:ch4:tonne")
    assert (butane["value"], butane["unit"], butane["gwp_set"]) == (2.52, "kg CO2e/tonne", "AR5")
    camx = row("egrid:2024:electricity:grid-electricity-egrid-total-output-emission-rate:camx:co2e:mwh")
    assert (camx["value"], camx["unit"]) == (183.7946376, "kg CO2e/MWh")
    grid = row("ember:2024:electricity:grid-electricity-lifecycle-intensity-of-generation:usa:co2e:kwh")
    assert (grid["value"], grid["unit"], grid["gwp_set"]) == (0.38381, "kg CO2e/kWh", "mixed")
    soy = row("useeio:v1.4:purchased-goods-and-services:soybean-farming-naics-111110-with-margins:usa:co2e:usd-2024-purchaser-price")
    assert soy["unit"] == "kg CO2e/USD (2024, purchaser price)"


def test_gwp_sets_are_never_silently_mixed(table):
    co2e = table.filter(pl.col("unit").str.starts_with("kg CO2e/"))
    assert "none" not in set(co2e["gwp_set"])
    mass = table.filter(pl.col("gwp_set") == "none")
    assert set(mass["gas"]) == {"CO2", "CH4", "N2O"} and not mass["unit"].str.contains("CO2e").any()
    sets = {s: set(g) for s, g in table.group_by("source").agg(pl.col("gwp_set").unique()).iter_rows()}
    assert sets == {
        "epa-hub": {"none", "AR4", "AR6"},  # per-gas factors, WARM waste (AR4), GWP table (AR6)
        "egrid": {"none", "AR5"},
        "uk-desnz": {"AR5"},
        "ember": {"mixed"},
        "useeio": {"AR6"},
    }


def test_the_hub_republishes_egrid_unchanged(table):
    pick = lambda source: table.filter(  # noqa: E731
        (pl.col("source") == source) & (pl.col("region") == "CAMX") & (pl.col("gas") == "CO2")
    )["value"].item()
    assert pick("epa-hub") == pick("egrid") == 183.0675168


def test_resources_find_the_current_desnz_flat_file(monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({ef.DESNZ_CONTENT_API: json_fixture("govuk_desnz_2026_content.json")}))
    urls = ef.resources(CollectContext(load_contract("carbondata/emission-factors"), NOW))
    assert urls["uk-desnz"] == FLAT_FILE  # the July 2026 revision, not the June original
    assert set(urls) == set(ef.BUILDERS)


def test_flat_file_link_must_be_unambiguous():
    page = json_fixture("govuk_desnz_2026_content.json")
    without = {"details": {"attachments": [a for a in page["details"]["attachments"] if "flat file" not in a["title"].lower()]}}
    with pytest.raises(ValueError, match="found 0"):
        ef.desnz_flat_file_url(without)


def upstream(raw):
    return Upstream({
        ef.HUB_URL: raw["epa-hub"],
        ef.EGRID_URL: raw["egrid"],
        ef.USEEIO_URL: raw["useeio"],
        FLAT_FILE: raw["uk-desnz"],
        ef.EMBER_URL: raw["ember"],
    })


def small(contract):
    """The fixtures hold a few hundred factors, not the ~16,000 of the full library."""
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=100))


def test_end_to_end_publish(store, monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({ef.DESNZ_CONTENT_API: json_fixture("govuk_desnz_2026_content.json")}))
    contract = small(load_contract("carbondata/emission-factors"))
    result = publish.run_dataset(contract, store, fetcher=upstream(emission_factor_raw()), now=NOW)
    assert result.outcome == "published", result.report
    manifest, _ = get_json(store, manifest_key("carbondata", "emission-factors"))
    doc, _ = get_json(store, f"sites/carbondata/datasets/emission-factors/versions/{manifest['current']}.json")
    assert doc["kind"] == "table" and doc["partition"] == "source" and doc["key"] == ["factor_id"]
    assert {f"table/{s}.json" for s in common.SOURCES} <= set(doc["files"])
    assert doc["coverage"]["end"] == "2026"
    assert doc["source"]["attribution"].count("EPA methodology, Cornerstone release (not EPA-official)") == 3
    egrid = json.loads(store.get(blob_key(doc["files"]["table/egrid.json"]["blob"]))[0])
    assert egrid["meta"]["partition"] == {"source": "egrid"}
    assert {r["region"] for r in egrid["rows"]} >= {"CAMX", "us-ca", "usa"}

    again = publish.run_dataset(contract, store, fetcher=upstream(emission_factor_raw()), now=NOW)
    assert again.outcome == "unchanged"


def test_a_truncated_library_is_blocked(store, monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({ef.DESNZ_CONTENT_API: json_fixture("govuk_desnz_2026_content.json")}))
    real = load_contract("carbondata/emission-factors")
    # With the real contract, a few hundred rows is a broken download, not a factor library.
    blocked = publish.run_dataset(real, store, fetcher=upstream(emission_factor_raw()), now=NOW)
    assert blocked.outcome == "blocked"
    assert any("the contract requires at least 12000" in e for e in blocked.report.errors)


def test_a_source_losing_most_of_its_rows_keeps_the_previous_version(store, monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({ef.DESNZ_CONTENT_API: json_fixture("govuk_desnz_2026_content.json")}))
    contract = small(load_contract("carbondata/emission-factors"))
    raw = emission_factor_raw()
    first = publish.run_dataset(contract, store, fetcher=upstream(raw), now=NOW)
    assert first.outcome == "published"

    # DESNZ republishes a flat file that has lost its factor rows (header and END marker only).
    sheet = cells("desnz_2026_flat_excerpt.csv")
    header_at = next(i for i, row in enumerate(sheet) if row[0] == "ID")
    raw["uk-desnz"] = xlsx({"Front page": cells("desnz_2026_front_page.csv"), "Factors by Category": sheet[: header_at + 1] + sheet[-1:]})
    later = datetime(2026, 10, 31, 6, 41, tzinfo=timezone.utc)
    second = publish.run_dataset(contract, store, fetcher=upstream(raw), now=later)
    assert second.outcome == "blocked"
    assert any("row count fell" in e for e in second.report.errors)
    manifest, _ = get_json(store, manifest_key("carbondata", "emission-factors"))
    assert manifest["current"] == first.version
