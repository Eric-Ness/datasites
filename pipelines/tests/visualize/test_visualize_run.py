"""boundaries end to end through run_dataset, and validation blocking bad builds."""

import dataclasses
import json

import pytest
from visualize_helpers import NOW, fetcher

from datasites_core.contracts import load_contract
from datasites_core.layout import blob_key, version_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_visualize import boundaries

FIXTURE_FEATURES = 23


@pytest.fixture
def contract():
    return load_contract("visualizedata/boundaries")


def with_min_rows(contract, n):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=n))


def test_contract_lists_every_layer(contract):
    assert contract.kind == "files" and contract.check_every == "P30D"
    assert sorted(contract.checks.required_files) == sorted([*(layer.path for layer in boundaries.LAYERS), "index.json"])
    assert contract.checks.min_rows == 7849


def test_publishes_into_the_store(store, contract, monkeypatch):
    monkeypatch.setattr(boundaries, "REQUIRE_FULL_COVERAGE", False)
    c = with_min_rows(contract, FIXTURE_FEATURES)
    result = run_dataset(c, store, fetcher=fetcher(), now=NOW)
    assert result.outcome == "published", result.report and result.report.errors
    doc = get_json(store, version_key(c.site, c.id, result.version))[0]
    assert doc["files"]["us/counties-20m.geojson"]["type"] == "application/geo+json"
    index = json.loads(store.get(blob_key(doc["files"]["index.json"]["blob"]))[0])
    assert index["feature_count"] == FIXTURE_FEATURES
    again = run_dataset(c, store, fetcher=fetcher(), now=NOW)
    assert again.outcome == "unchanged"


def test_too_few_features_blocks(store, contract, monkeypatch):
    # The real contract wants the full 7,849 features; a subset must not replace them.
    monkeypatch.setattr(boundaries, "REQUIRE_FULL_COVERAGE", False)
    result = run_dataset(contract, store, fetcher=fetcher(), now=NOW)
    assert result.outcome == "blocked"
    assert any("requires at least 7849" in e for e in result.report.errors)


def test_a_missing_layer_blocks(store, contract, monkeypatch):
    # e.g. a layer dropped from LAYERS by mistake: its required file is missing, so nothing is published
    monkeypatch.setattr(boundaries, "REQUIRE_FULL_COVERAGE", False)
    monkeypatch.setattr(boundaries, "LAYERS", [la for la in boundaries.LAYERS if la.path != "us/counties-5m.geojson"])
    result = run_dataset(with_min_rows(contract, 1), store, fetcher=fetcher(), now=NOW)
    assert result.outcome == "blocked"
    assert any("required files missing" in e and "us/counties-5m.geojson" in e for e in result.report.errors)
