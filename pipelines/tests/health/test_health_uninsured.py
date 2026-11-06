"""healthdata/uninsured: Census SAHIE (verbatim excerpts of sahie_2013.csv and sahie_2024.csv, rezipped)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import polars as pl
import pytest
from health_helpers import sahie

from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import version_key
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_health import sahie as pipeline

NOW = datetime(2026, 11, 6, 15, 37, tzinfo=timezone.utc)


@pytest.fixture
def table():
    return pipeline.transform({"sahie-2013": sahie(2013), "sahie-2024": sahie(2024)})


def at(table, series, location, year):
    rows = table.filter((pl.col("series") == series) & (pl.col("location") == location) & (pl.col("date") == str(year)))
    assert len(rows) == 1, (series, location, year)
    return rows.row(0, named=True)


def test_shape_and_validation(table):
    contract = load_contract("healthdata/uninsured")
    assert table.columns == contract.columns
    lenient = dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=5, max_null_fraction={}))
    assert validate(lenient, table, None).status == "pass"


def test_values_for_all_races_sexes_and_incomes(table):
    la = at(table, "under-65", "us-county-06037", 2024)
    assert (la["uninsured_pct"], la["uninsured_pct_moe"], la["uninsured"], la["population"]) == (8.3, 0.2, 671007, 8039276)
    assert at(table, "children-under-19", "us-county-06037", 2024)["uninsured_pct"] == 3.1
    ca = at(table, "under-65", "us-ca", 2024)  # a state row (geocat 40)
    assert (ca["uninsured_pct"], ca["uninsured"]) == (6.9, 2247123)
    # Men only and low-income rows are in the excerpt but not kept.
    assert len(table.filter(pl.col("location") == "us-county-06037")) == 6


def test_2013_uses_the_updated_estimates(table):
    assert at(table, "under-65", "us-county-06037", 2013)["uninsured_pct"] == 23.7
    # A file holding both versions keeps "Updated" only.
    both = sahie(2013, edit=lambda t: t + t.splitlines(keepends=True)[-5].replace("Updated ", "Original", 1).replace(" 23.7,", " 99.9,", 1))
    again = pipeline.transform({"sahie-2013": both})
    assert at(again, "under-65", "us-county-06037", 2013)["uninsured_pct"] == 23.7


def test_kalawao_is_missing(table):
    kalawao = at(table, "under-65", "us-county-15005", 2024)
    assert kalawao["uninsured_pct"] is None and kalawao["population"] is None


def test_changed_files_stop_the_run():
    with pytest.raises(ValueError, match="column header changed"):
        pipeline.transform({"sahie-2024": sahie(2024, edit=lambda t: t.replace("PCTUI,pctui_moe", "PCTUN,pctun_moe", 1))})
    with pytest.raises(ValueError, match="rows for years"):
        pipeline.transform({"sahie-2023": sahie(2024).replace(b"sahie_2024.csv", b"sahie_2023.csv")})
    with pytest.raises(ValueError, match="non-numeric NUI"):
        pipeline.transform({"sahie-2024": sahie(2024, edit=lambda t: t.replace(",  671007,", ",  n/a,", 1))})


def test_resources_lists_every_year():
    listing = "".join(f'<a href="sahie-{y}-csv.zip">sahie-{y}-csv.zip</a>' for y in range(2008, 2025))
    ctx = type("Ctx", (), {"get": lambda self, url, **_: listing.encode()})()
    res = pipeline.resources(ctx)
    assert len(res) == 17 and res["sahie-2024"].endswith("/sahie-2024-csv.zip")
    gap = type("Ctx", (), {"get": lambda self, url, **_: listing.replace("sahie-2015-csv.zip", "").encode()})()
    with pytest.raises(ValueError, match="missing for \\[2015\\]"):
        pipeline.resources(gap)


def test_end_to_end_publish(store, monkeypatch):
    contract = load_contract("healthdata/uninsured")
    contract = dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=5, max_null_fraction={}))
    files = {f"{pipeline.BASE}/sahie-{y}-csv.zip": sahie(y) for y in (2013, 2024)}
    monkeypatch.setattr(pipeline, "resources", lambda ctx: {f"sahie-{y}": f"{pipeline.BASE}/sahie-{y}-csv.zip" for y in (2013, 2024)})

    def fetcher(url, etag, last_modified):
        if last_modified:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, files[url], None, "Thu, 06 Aug 2026 13:02:36 GMT")

    first = publish.run_dataset(contract, store, fetcher=fetcher, now=NOW)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("healthdata", "uninsured", first.version))
    assert doc["coverage"] == {"start": "2013", "end": "2024"}
    assert "under-65/us-county-06037.json" in doc["files"]
    assert publish.run_dataset(contract, store, fetcher=fetcher, now=NOW.replace(day=13)).outcome == "unchanged"
