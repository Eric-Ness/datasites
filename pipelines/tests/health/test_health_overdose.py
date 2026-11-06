"""healthdata/overdose-deaths and overdose-deaths-county: CDC VSRR provisional overdose deaths (verbatim excerpts)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import polars as pl
import pytest
from health_helpers import body

from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import version_key
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_health import overdose_counties, overdose_states, vsrr_overdose

NOW = datetime(2026, 11, 6, 15, 37, tzinfo=timezone.utc)


@pytest.fixture
def states():
    return vsrr_overdose.transform_states(body("vsrr_state_excerpt.csv"))


@pytest.fixture
def counties():
    return vsrr_overdose.transform_counties(body("vsrr_county_excerpt.csv"))


def at(table, series, location, date):
    rows = table.filter((pl.col("series") == series) & (pl.col("location") == location) & (pl.col("date") == date))
    assert len(rows) == 1, (series, location, date)
    return rows.row(0, named=True)


def lenient(contract, min_rows=5):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=min_rows, max_null_fraction={}))


def test_state_shape_and_validation(states):
    contract = load_contract("healthdata/overdose-deaths")
    assert states.columns == contract.columns
    assert set(states["series"]) == set(contract.series)
    assert validate(lenient(contract), states, None).status == "pass"


def test_national_figures_and_flags(states):
    us = at(states, "all-drugs", "usa", "2024-12")
    assert (us["deaths"], us["predicted_deaths"], us["percent_complete"]) == (80860, 81313, 100.0)
    assert us["pct_drugs_specified"] == pytest.approx(96.099431)
    latest = at(states, "all-drugs", "usa", "2026-05")
    assert (latest["deaths"], latest["predicted_deaths"], latest["underreported"]) == (65351, 66478, True)
    assert at(states, "synthetic-opioids", "usa", "2024-12")["deaths"] == 48662
    assert at(states, "all-causes", "usa", "2026-05")["deaths"] == 3057425


def test_new_york_adds_up_new_york_city(states):
    ny = at(states, "all-drugs", "us-ny", "2024-12")
    assert ny["deaths"] == 2331 + 2254  # New York excluding NYC + New York City
    assert ny["percent_complete"] is None and ny["pct_drugs_specified"] is None
    assert states.filter(pl.col("location").is_in(["us-yc", "YC", "NY"])).is_empty()


def test_withheld_counts_are_null_and_flagged(states):
    heroin = at(states, "heroin", "us-vt", "2026-05")  # "Underreported ... Data suppressed (<10)."
    assert heroin["deaths"] is None and heroin["suppressed"] and heroin["underreported"]
    assert at(states, "all-drugs", "us-vt", "2024-12")["deaths"] == 205


def test_county_values_and_suppression(counties):
    contract = load_contract("healthdata/overdose-deaths-county")
    assert validate(lenient(contract), counties, None).status == "pass"
    assert at(counties, "all-drugs", "us-county-06037", "2026-03")["deaths"] == 1810
    tiny = at(counties, "all-drugs", "us-county-02016", "2024-12")  # Aleutians West: 1-9 deaths, withheld
    assert tiny["deaths"] is None and tiny["suppressed"]
    assert at(counties, "all-drugs", "us-county-02016", "2026-03")["deaths"] == 0  # a real zero is shown
    assert at(counties, "all-drugs", "us-county-09001", "2024-12")["deaths"] == 172  # Connecticut's former county


def test_a_county_listed_twice_counts_once(counties):
    # Bedford, VA appears again as "Bedford (city)" under the same code with the same figures.
    assert len(counties.filter(pl.col("location") == "us-county-51019")) == 2
    clashing = body("vsrr_county_excerpt.csv").decode().replace(",Bedford (city),51019,51,19,3,26,", ",Bedford (city),51019,51,19,3,27,", 1)
    assert clashing != body("vsrr_county_excerpt.csv").decode()
    with pytest.raises(ValueError, match="listed twice with different figures"):
        vsrr_overdose.transform_counties(clashing.encode())


def test_changed_tables_stop_the_run():
    good = body("vsrr_state_excerpt.csv").decode()
    with pytest.raises(ValueError, match="columns changed"):
        vsrr_overdose.transform_states(good.replace("Predicted Value", "Prediction", 1).encode())
    with pytest.raises(ValueError, match="unknown indicators"):
        vsrr_overdose.transform_states(good.replace("Cocaine (T40.5)", "Cocaine", 1).encode())
    with pytest.raises(ValueError, match="non-numeric"):
        vsrr_overdose.transform_states(good.replace(",1204,100,", ",n/a,100,", 1).encode())
    only_ny = "\n".join(line for line in good.splitlines() if not line.startswith("YC,")) + "\n"
    with pytest.raises(ValueError, match="New York without New York City"):
        vsrr_overdose.transform_states(only_ny.encode())


@pytest.mark.parametrize(
    ("module", "dataset", "fixture"),
    [(overdose_states, "overdose-deaths", "vsrr_state_excerpt.csv"), (overdose_counties, "overdose-deaths-county", "vsrr_county_excerpt.csv")],
)
def test_end_to_end_publish(store, module, dataset, fixture):
    contract = lenient(load_contract(f"healthdata/{dataset}"))
    (url,) = module.RESOURCES.values()
    etag = 'W/"abc"'

    def fetcher(requested, if_etag, last_modified):
        assert requested == url
        if if_etag == etag:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, body(fixture), etag, "Wed, 14 Oct 2026 14:05:28 GMT")

    first = publish.run_dataset(contract, store, fetcher=fetcher, now=NOW)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("healthdata", dataset, first.version))
    assert doc["source"]["license"] == "LicenseRef-US-Government-Work"
    second = publish.run_dataset(contract, store, fetcher=fetcher, now=NOW.replace(day=7))
    assert second.outcome == "unchanged"
