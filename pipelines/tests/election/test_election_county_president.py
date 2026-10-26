"""electiondata/county-president: MEDSL's county presidential returns 2000-2024 (fixture: whole counties copied from V20)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone

import polars as pl
import pytest
from election_helpers import FakeContext, fixture_bytes, fixture_json

from datasites_core import fetch, publish
from datasites_core.contracts import load_contract
from datasites_core.layout import version_key
from datasites_core.store import get_json
from datasites_core.validate import validate_table
from datasites_election import medsl, medsl_county_president

FIXTURE = "medsl_countypres_excerpt.csv"
META = "dataverse_countypres_metadata.json"
META_KEY = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/VOQCHQ"
NOW = datetime(2026, 10, 26, 15, 37, tzinfo=timezone.utc)


@pytest.fixture
def contract():
    return load_contract("electiondata/county-president")


@pytest.fixture
def table():
    return medsl_county_president.transform({"countypres": fixture_bytes(FIXTURE)})


def county(table, year, state, name):
    rows = table.filter((pl.col("year") == year) & (pl.col("state_po") == state) & (pl.col("county_name") == name))
    return {(r["unit_id"], r["party"]) if name == "BEDFORD" else r["party"]: r for r in rows.iter_rows(named=True)}


def for_excerpt(contract):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=50, max_null_fraction={}))


def excerpt_meta() -> dict:
    """The real V20 metadata, with the data file's MD5 swapped for the excerpt's."""
    meta = fixture_json(META)
    for item in meta["data"]["latestVersion"]["files"]:
        if item["dataFile"].get("originalFileName") == medsl_county_president.FILENAME:
            item["dataFile"]["md5"] = hashlib.md5(fixture_bytes(FIXTURE)).hexdigest()
    return meta


def test_shape_matches_contract_and_validates(contract, table):
    assert table.columns == contract.columns
    assert len(table) == 56
    assert table.select(contract.key).is_duplicated().sum() == 0
    assert "56 rows, the contract requires at least 75000" in validate_table(contract, table, None).errors
    assert validate_table(for_excerpt(contract), table, None).status == "pass"


def test_a_county_with_total_rows(table):
    autauga = county(table, "2024", "AL", "AUTAUGA")
    assert autauga["REPUBLICAN"]["candidate"] == "DONALD J TRUMP" and autauga["REPUBLICAN"]["candidatevotes"] == 20484
    assert autauga["DEMOCRAT"]["candidatevotes"] == 7439
    assert sum(r["candidatevotes"] for r in autauga.values()) == autauga["DEMOCRAT"]["totalvotes"] == 28281
    row = autauga["DEMOCRAT"]
    assert row["county_fips"] == "01001" and row["county_id"] == "us-county-01001" and row["unit_id"] == "01001"
    assert row["state_id"] == "us-al" and row["version"] == "2026-02-25" and row["from_modes"] is False


def test_counties_reported_only_by_mode_are_summed(table):
    # North Carolina 2024: four unlabeled mode rows per candidate and no TOTAL row.
    alamance = county(table, "2024", "NC", "ALAMANCE")
    assert {p: r["candidatevotes"] for p, r in alamance.items()} == {
        "LIBERTARIAN": 305,
        "REPUBLICAN": 47937,
        "DEMOCRAT": 40624,
        "OTHER": 965,
    }
    assert sum(r["candidatevotes"] for r in alamance.values()) == 89831
    assert all(r["from_modes"] for r in alamance.values())
    # Georgia 2020: ABSENTEE, ADVANCED VOTING, ELECTION DAY and PROV.
    appling = county(table, "2020", "GA", "APPLING")
    assert sum(r["candidatevotes"] for r in appling.values()) == appling["DEMOCRAT"]["totalvotes"] == 8341
    assert appling["REPUBLICAN"]["candidatevotes"] == 6526 and appling["REPUBLICAN"]["from_modes"] is True


def test_mode_rows_are_dropped_where_a_total_exists(table):
    # Travis County 2024 has TOTAL rows, EARLY VOTING rows and a TOTAL VOTES CAST row.
    travis = county(table, "2024", "TX", "TRAVIS")
    assert {p: r["candidatevotes"] for p, r in travis.items()} == {
        "LIBERTARIAN": 4631,
        "REPUBLICAN": 170787,
        "DEMOCRAT": 398981,
        "OTHER": 6877,
    }
    assert not any(r["from_modes"] for r in travis.values())
    assert "TOTAL VOTES CAST" not in table["candidate"].to_list()


def test_rows_that_arent_candidates_are_dropped(table):
    # Iowa 2024 lists UNDERVOTES and OVERVOTES, which MEDSL's totalvotes includes.
    adair = county(table, "2024", "IA", "ADAIR")
    assert set(adair) == {"REPUBLICAN", "DEMOCRAT", "OTHER", "LIBERTARIAN"}
    assert sum(r["candidatevotes"] for r in adair.values()) == 4080 < adair["DEMOCRAT"]["totalvotes"] == 4104


def test_two_other_rows_are_added_together(table):
    alpine = county(table, "2024", "CA", "ALPINE")
    assert alpine["OTHER"]["candidatevotes"] == 14
    assert sum(r["candidatevotes"] for r in alpine.values()) == 738


def test_reporting_units_and_fips_codes(table):
    district = county(table, "2000", "AK", "DISTRICT 14")
    assert district["GREEN"]["candidate"] == "RALPH NADER" and district["GREEN"]["candidatevotes"] == 342
    assert district["GREEN"]["county_fips"] is None and district["GREEN"]["unit_id"] == "AK-DISTRICT-14"
    kc = county(table, "2024", "MO", "KANSAS CITY")["DEMOCRAT"]
    assert kc["county_fips"] is None and kc["county_id"] is None and kc["unit_id"] == "MO-KANSAS-CITY"
    assert county(table, "2000", "CT", "STATEWIDE WRITEIN")["OTHER"]["unit_id"] == "CT-STATEWIDE-WRITEIN"
    # Connecticut's former counties keep their FIPS codes but aren't in the registry.
    fairfield = county(table, "2000", "CT", "FAIRFIELD")["DEMOCRAT"]
    assert fairfield["county_fips"] == "09001" and fairfield["county_id"] is None
    # Oglala Lakota County: 46102 in 2016, 46113 (its former code) in MEDSL's 2024 rows.
    assert county(table, "2016", "SD", "OGLALA LAKOTA")["DEMOCRAT"]["county_fips"] == "46102"
    lakota = county(table, "2024", "SD", "OGLALA LAKOTA")["DEMOCRAT"]
    assert lakota["county_fips"] == "46102" and lakota["county_id"] == "us-county-46102" and lakota["candidatevotes"] == 2567


def test_a_county_and_a_city_with_the_same_name(table):
    bedford = county(table, "2016", "VA", "BEDFORD")
    assert bedford[("51019", "REPUBLICAN")]["candidatevotes"] == 30659
    assert bedford[("51019", "REPUBLICAN")]["county_id"] == "us-county-51019"
    # Bedford City became part of Bedford County in 2013; MEDSL keeps an empty row for it.
    assert bedford[("51515", "REPUBLICAN")]["candidatevotes"] == 0 and bedford[("51515", "REPUBLICAN")]["county_id"] is None


def test_na_counts_become_null(table):
    de_baca = county(table, "2024", "NM", "DE BACA")
    assert de_baca["LIBERTARIAN"]["candidate"] == "CHASE OLIVER" and de_baca["LIBERTARIAN"]["candidatevotes"] is None
    assert de_baca["REPUBLICAN"]["candidatevotes"] == 649


def test_changed_columns_or_codes_stop_the_run():
    body = fixture_bytes(FIXTURE)
    with pytest.raises(ValueError, match="columns changed"):
        medsl_county_president.transform({"countypres": body.replace(b'"mode"', b'"vote_mode"', 1)})
    with pytest.raises(ValueError, match="unexpected party"):
        medsl_county_president.transform({"countypres": body.replace(b'"DEMOCRAT"', b'"DEMOCRATIC"', 1)})
    with pytest.raises(ValueError, match="no party"):
        medsl_county_president.transform({"countypres": body.replace(b'"CHASE OLIVER","LIBERTARIAN"', b'"CHASE OLIVER",""', 1)})
    # A mode row that no longer adds up to the county's total.
    with pytest.raises(ValueError, match="don't add up"):
        medsl_county_president.transform({"countypres": body.replace(b'"REPUBLICAN",11562,89831', b'"REPUBLICAN",11563,89831', 1)})


def test_collect_checks_the_copy_against_the_cited_version(contract, tmp_path, monkeypatch):
    monkeypatch.setattr(medsl, "local_dir", lambda: tmp_path)
    (tmp_path / medsl_county_president.FILENAME).write_bytes(fixture_bytes(FIXTURE))
    ctx = FakeContext(contract, {META_KEY: json.dumps(excerpt_meta()).encode()})
    assert medsl_county_president.collect(ctx) == {"countypres": fixture_bytes(FIXTURE)}
    # The real V20 checksum doesn't match the excerpt: a stale or damaged copy stops the run.
    ctx = FakeContext(contract, {META_KEY: json.dumps(fixture_json(META)).encode()})
    with pytest.raises(RuntimeError, match="doesn't match countypres_2000-2024.csv"):
        medsl_county_president.collect(ctx)
    meta = excerpt_meta()
    meta["data"]["latestVersion"]["versionNumber"] = 21
    with pytest.raises(RuntimeError, match="published V21.0 but the contract cites V20.0"):
        medsl_county_president.collect(FakeContext(contract, {META_KEY: json.dumps(meta).encode()}))
    (tmp_path / medsl_county_president.FILENAME).unlink()
    with pytest.raises(FileNotFoundError, match="download it by hand"):
        medsl_county_president.collect(ctx)


def test_end_to_end_publish(contract, store, tmp_path, monkeypatch):
    monkeypatch.setattr(medsl, "local_dir", lambda: tmp_path)
    (tmp_path / medsl_county_president.FILENAME).write_bytes(fixture_bytes(FIXTURE))
    meta = json.dumps(excerpt_meta()).encode()
    monkeypatch.setattr(fetch.CollectContext, "get", lambda self, url, **kw: meta)
    first = publish.run_dataset(for_excerpt(contract), store, now=NOW)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("electiondata", "county-president", first.version))
    assert doc["coverage"] == {"start": "2000", "end": "2024"}
    assert doc["key"] == ["year", "state_po", "unit_id", "candidate", "party"]
    assert doc["files"]["table/2024.json"]["rows"] == 8 * 4  # eight 2024 counties, four candidate lines each
    second = publish.run_dataset(for_excerpt(contract), store, now=NOW.replace(day=31))
    assert second.outcome == "unchanged"
