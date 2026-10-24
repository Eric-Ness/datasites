import dataclasses
import json
from datetime import datetime, timezone

import pytest
from carbon_helpers import Upstream, body, fake_api, json_fixture

from datasites_carbon import gcb_fossil_co2 as gcb
from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.layout import blob_key
from datasites_core.store import get_json

NOW = datetime(2026, 10, 24, 6, 41, tzinfo=timezone.utc)
NAME = "GCB2025v15_MtCO2_flat.csv"
URL = "https://zenodo.org/api/records/17417124/files/GCB2025v15_MtCO2_flat.csv/content"


@pytest.fixture(scope="module")
def table():
    return gcb.transform({NAME: body("gcb2025v15_mtco2_flat_excerpt.csv")})


def value(table, series, location, year):
    found = table.filter((table["series"] == series) & (table["location"] == location) & (table["date"] == year))
    assert len(found) <= 1
    return found["value"].item() if len(found) else None


def test_shape(table):
    contract = load_contract("carbondata/national-fossil-co2")
    assert table.columns == contract.columns == ["series", "location", "date", "value"]
    assert set(table["series"]) == set(contract.series)


def test_values_and_location_ids(table):
    assert value(table, "total", "usa", "2024") == 4904.119652
    assert value(table, "coal", "chn", "2024") == 8886.02066
    assert value(table, "total", "world", "2024") == 38598.578033  # GCB's "Global"
    assert value(table, "oil", "gcb-international-aviation", "2024") == 569.039317
    assert value(table, "total", "gcb-international-shipping", "2024") == 623.424509
    assert value(table, "total", "gcb-kuwaiti-oil-fires", "1991") == 477.924832
    assert value(table, "total", "gcb-kosovo", "2024") == 8.04994  # KSV is not an ISO 3166 code


def test_blank_cells_are_not_rows(table):
    # Afghanistan 1750 has only a cement value (0); its other cells are blank
    afg = table.filter((table["location"] == "afg") & (table["date"] == "1750"))
    assert afg.select("series", "value").rows() == [("cement", 0.0)]
    assert value(table, "gas", "gcb-kosovo", "2024") is None
    assert table["value"].null_count() == 0


def test_every_location_is_allowed_by_the_contract(table):
    contract = load_contract("carbondata/national-fossil-co2")
    known = contract.locations.names()
    assert all(contract.locations.allows(loc, known) for loc in table["location"].unique())
    assert set(gcb.SPECIAL.values()) - {"world"} == set(contract.locations.explicit)


def test_latest_release_is_found_in_the_zenodo_record():
    record = json_fixture("zenodo_17417124_latest.json")
    assert gcb.latest_file(record) == (NAME, URL)
    with pytest.raises(ValueError, match="expected one GCB"):
        gcb.latest_file({"id": 1, "files": [f for f in record["files"] if f["key"] != NAME]})


def test_resources_name_the_release(monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({gcb.LATEST: json_fixture("zenodo_17417124_latest.json")}))
    assert gcb.resources(CollectContext(load_contract("carbondata/national-fossil-co2"), NOW)) == {NAME: URL}


def small(contract):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=10))


def test_end_to_end_publish(store, monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({gcb.LATEST: json_fixture("zenodo_17417124_latest.json")}))
    contract = small(load_contract("carbondata/national-fossil-co2"))
    result = publish.run_dataset(contract, store, fetcher=Upstream({URL: body("gcb2025v15_mtco2_flat_excerpt.csv")}), now=NOW)
    assert result.outcome == "published", result.report
    doc, _ = get_json(store, f"sites/carbondata/datasets/national-fossil-co2/versions/{result.version}.json")
    assert [r["name"] for r in doc["source"]["raw"]] == [NAME]  # the version records which GCB release it holds
    assert doc["locations"]["gcb-international-aviation"] == "International aviation (bunker fuels)"
    assert doc["locations"]["usa"] == "United States"
    usa = json.loads(store.get(blob_key(doc["files"]["total/usa.json"]["blob"]))[0])
    assert usa["data"] == [{"date": "2023", "value": 4918.406685}, {"date": "2024", "value": 4904.119652}]
    assert usa["meta"]["units"] == {"value": "MtCO2"}


def test_a_new_unlisted_entity_blocks_the_release(store, monkeypatch):
    monkeypatch.setattr(CollectContext, "get_json", fake_api({gcb.LATEST: json_fixture("zenodo_17417124_latest.json")}))
    contract = small(load_contract("carbondata/national-fossil-co2"))
    lines = body("gcb2025v15_mtco2_flat_excerpt.csv").decode().splitlines()
    added = lines + ['"Antarctic Fishing Fleet",,9999,2024,1.5,,1.5,,,,,']
    result = publish.run_dataset(contract, store, fetcher=Upstream({URL: "\n".join(added).encode()}), now=NOW)
    assert result.outcome == "blocked"
    assert any("gcb-antarctic-fishing-fleet" in e for e in result.report.errors)
