"""electiondata/house-results: MEDSL's U.S. House returns 1976-2024 (fixture: whole races copied from V15)."""

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
from datasites_election import medsl, medsl_house

FIXTURE = "medsl_house_excerpt.csv"
META = "dataverse_house_metadata.json"
META_KEY = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/IG0UN2"
NOW = datetime(2026, 10, 26, 15, 37, tzinfo=timezone.utc)


@pytest.fixture
def contract():
    return load_contract("electiondata/house-results")


@pytest.fixture
def table():
    return medsl_house.transform({"house": fixture_bytes(FIXTURE)})


def race(table, year, state, district, stage="GEN", special=False):
    return table.filter(
        (pl.col("year") == year)
        & (pl.col("state_po") == state)
        & (pl.col("district") == district)
        & (pl.col("stage") == stage)
        & (pl.col("special") == special)
    ).sort("race_row")


def for_excerpt(contract):
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=20, max_null_fraction={}))


def excerpt_meta() -> dict:
    """The real V15 metadata, with the data file's MD5 swapped for the excerpt's."""
    meta = fixture_json(META)
    for item in meta["data"]["latestVersion"]["files"]:
        if item["dataFile"]["filename"] == medsl_house.DATAVERSE_NAME:
            item["dataFile"]["md5"] = hashlib.md5(fixture_bytes(FIXTURE)).hexdigest()
    return meta


def test_shape_matches_contract_and_validates(contract, table):
    assert table.columns == contract.columns
    assert len(table) == 32
    assert table.select(contract.key).is_duplicated().sum() == 0
    assert "32 rows, the contract requires at least 33000" in validate_table(contract, table, None).errors
    assert validate_table(for_excerpt(contract), table, None).status == "pass"


def test_values_and_codes(table):
    me = race(table, "2018", "ME", 2)
    assert me.select("candidate", "party", "candidatevotes").rows() == [
        ("BRUCE POLIQUIN", "REPUBLICAN", 136326),
        ("JARED F GOLDEN", "DEMOCRAT", 139231),
    ]
    assert set(me["totalvotes"]) == {281371}  # includes ballots exhausted in the ranked-choice count
    at_large = race(table, "2024", "AK", 0)
    assert at_large["state_id"].unique().to_list() == ["us-ak"]
    assert at_large["candidatevotes"].max() == 159550 and set(at_large["totalvotes"]) == {329555}
    writein = at_large.filter(pl.col("candidate") == "WRITEIN").row(0, named=True)
    assert writein["writein"] is True and writein["party"] is None and writein["candidate_total_votes"] is None
    row = race(table, "2022", "CA", 45).row(0, named=True)
    assert row["state_fips"] == 6 and row["state_cen"] == 93 and row["state_ic"] == 71
    assert row["version"] == "2025-09-10" and row["mode"] == "TOTAL" and row["office"] == "US HOUSE"


def test_uncontested_races_with_no_count_become_null(table):
    for year, state, district in [("1976", "FL", 1), ("2016", "FL", 24), ("2020", "FL", 25), ("2024", "FL", 20)]:
        row = race(table, year, state, district).row(0, named=True)
        assert row["candidatevotes"] is None and row["totalvotes"] is None, (year, state, district)
    assert race(table, "2020", "FL", 25)["candidate"].item() == "MARIO DIAZ-BALART"
    # Small real counts are kept: write-in lines of 10 and 28 votes in Maryland 2012.
    assert sorted(race(table, "2012", "MD", 7)["candidatevotes"].to_list())[:2] == [10, 28]


def test_fusion_lines_and_candidate_totals(table):
    ny = race(table, "2024", "NY", 1)
    lalota = ny.filter(pl.col("candidate") == "NICK LALOTA").sort("party")
    assert lalota.select("party", "fusion_ticket", "candidatevotes").rows() == [
        ("CONSERVATIVE", True, 25483),
        ("REPUBLICAN", False, 200802),
    ]
    assert set(lalota["candidate_total_votes"]) == {226285}
    assert set(ny.filter(pl.col("candidate") == "JOHN P. AVLON")["candidate_total_votes"]) == {181647 + 1893}
    assert {"BLANK", "VOID", "SCATTERING"} <= set(ny["candidate"].to_list())


def test_texas_1996_special_primaries_and_runoffs(table):
    primary = race(table, "1996", "TX", 8, stage="PRI", special=True)
    assert len(primary) == 4 and set(primary["totalvotes"]) == {193659}
    runoff = race(table, "1996", "TX", 8)
    assert runoff["runoff"].to_list() == [True, True]
    assert sorted(runoff["candidatevotes"].to_list()) == [21004, 30366]
    # 2006-2018 don't record runoffs.
    assert race(table, "2018", "ME", 2)["runoff"].null_count() == 2


def test_changed_columns_or_codes_stop_the_run():
    body = fixture_bytes(FIXTURE)
    with pytest.raises(ValueError, match="columns changed"):
        medsl_house.transform({"house": body.replace(b"fusion_ticket", b"fusion", 1)})
    with pytest.raises(ValueError, match="unexpected stage"):
        medsl_house.transform({"house": body.replace(b",GEN,", b",GENERAL,", 1)})
    with pytest.raises(ValueError, match="year or district"):
        medsl_house.transform({"house": body.replace(b",US HOUSE,1,", b",US HOUSE,AT-LARGE,", 1)})
    with pytest.raises(ValueError, match="negative"):
        medsl_house.transform({"house": body.replace(b",139231,281371,", b",-139231,281371,", 1)})


def test_collect_checks_the_copy_against_the_cited_version(contract, tmp_path, monkeypatch):
    monkeypatch.setattr(medsl, "local_dir", lambda: tmp_path)
    (tmp_path / medsl_house.FILENAME).write_bytes(fixture_bytes(FIXTURE))
    ctx = FakeContext(contract, {META_KEY: json.dumps(excerpt_meta()).encode()})
    assert medsl_house.collect(ctx) == {"house": fixture_bytes(FIXTURE)}
    with pytest.raises(RuntimeError, match="doesn't match 1976-2024-house.tab"):
        medsl_house.collect(FakeContext(contract, {META_KEY: json.dumps(fixture_json(META)).encode()}))


def test_end_to_end_publish(contract, store, tmp_path, monkeypatch):
    monkeypatch.setattr(medsl, "local_dir", lambda: tmp_path)
    (tmp_path / medsl_house.FILENAME).write_bytes(fixture_bytes(FIXTURE))
    meta = json.dumps(excerpt_meta()).encode()
    monkeypatch.setattr(fetch.CollectContext, "get", lambda self, url, **kw: meta)
    first = publish.run_dataset(for_excerpt(contract), store, now=NOW)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("electiondata", "house-results", first.version))
    assert doc["coverage"] == {"start": "1976", "end": "2024"}
    assert doc["key"] == ["year", "state_po", "district", "stage", "special", "race_row"]
    assert doc["files"]["table/2024.json"]["rows"] == 5 + 1 + 7
