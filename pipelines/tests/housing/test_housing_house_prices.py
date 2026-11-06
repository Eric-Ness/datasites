"""housingdata/house-prices: FHFA's annual all-transactions HPI for counties and states (fixtures: whole areas)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import polars as pl
import pytest
from housing_helpers import cells, fhfa
from sheet_helpers import xlsx

from datasites_core import publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import version_key
from datasites_core.store import get_json
from datasites_core.validate import validate
from datasites_housing import fhfa_hpi

NOW = datetime(2026, 11, 6, 15, 37, tzinfo=timezone.utc)


@pytest.fixture
def contract():
    return load_contract("housingdata/house-prices")


@pytest.fixture
def table():
    return fhfa_hpi.transform({"county": fhfa("county"), "state": fhfa("state")})


def at(table, location, year):
    rows = table.filter((pl.col("location") == location) & (pl.col("date") == year))
    assert len(rows) == 1, (location, year)
    return rows.row(0, named=True)


def for_excerpt(contract):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=50))


def test_shape_and_validation(contract, table):
    assert table.columns == ["series", "location", "date", "annual_change", "hpi", "hpi_1990", "hpi_2000"]
    assert set(table["series"]) == {"all-transactions"}
    assert set(table["location"]) == {"us-county-01001", "us-county-06037", "us-county-27053", "us-county-36049", "us-ca", "us-mn"}
    report = validate(for_excerpt(contract), table, None)
    assert report.status == "pass", report.as_dict()


def test_values_match_the_workbook(table):
    la = at(table, "us-county-06037", "2025")
    assert (la["annual_change"], la["hpi"], la["hpi_1990"], la["hpi_2000"]) == (1.7, 2386.19, 410.35, 403.74)
    ca = at(table, "us-ca", "2025")
    assert (ca["annual_change"], ca["hpi"]) == (0.75, 1967.71)
    first = at(table, "us-county-01001", "1986")  # Autauga's first year: index 100, no change
    assert first["hpi"] == 100 and first["annual_change"] is None and first["hpi_1990"] == 95.45
    assert table["date"].min() == "1975"  # California's state index starts in 1975


def test_missing_values_stay_null_and_empty_years_are_dropped(table):
    lewis = table.filter(pl.col("location") == "us-county-36049")
    assert "2023" not in lewis["date"].to_list()  # FHFA left the whole year empty
    after = at(table, "us-county-36049", "2024")
    assert after["hpi"] == 264.3 and after["annual_change"] is None and after["hpi_1990"] is None


def test_a_changed_workbook_stops_the_run():
    rows = cells("fhfa_hpi_at_county_excerpt.csv")
    header = next(i for i, r in enumerate(rows) if r[:1] == ["State"])

    def edited(row: int, col: int, value: str) -> bytes:
        changed = [r[:] for r in rows]
        changed[row][col] = value
        return xlsx({"county": changed})

    with pytest.raises(ValueError, match="header changed"):
        fhfa_hpi.transform({"county": edited(header, 4, "Change (%)"), "state": fhfa("state")})
    with pytest.raises(ValueError, match="not in the registry"):
        fhfa_hpi.transform({"county": edited(header + 1, 2, "99999"), "state": fhfa("state")})
    with pytest.raises(ValueError, match="non-numeric HPI values"):
        fhfa_hpi.transform({"county": edited(header + 2, 5, "n/a"), "state": fhfa("state")})
    # "." (FHFA's old marker) and blank text cells are missing values, not errors.
    assert fhfa_hpi.transform({"county": edited(header + 2, 5, "."), "state": fhfa("state")})["hpi"].null_count() == 1


def test_end_to_end_publish(contract, store):
    bodies = {fhfa_hpi.RESOURCES["county"]: fhfa("county"), fhfa_hpi.RESOURCES["state"]: fhfa("state")}

    def fetcher(url, etag, last_modified):
        return Fetched(url, 200, bodies[url], None, None)

    first = publish.run_dataset(for_excerpt(contract), store, fetcher=fetcher, now=NOW)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("housingdata", "house-prices", first.version))
    assert doc["coverage"] == {"start": "1975", "end": "2025"}
    assert "all-transactions/us-county-06037.json" in doc["files"]
    second = publish.run_dataset(for_excerpt(contract), store, fetcher=fetcher, now=NOW.replace(day=20))
    assert second.outcome == "unchanged"
