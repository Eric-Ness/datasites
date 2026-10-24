"""electiondata/senate-results: MEDSL's U.S. Senate statewide returns 1976-2024 (fixture: whole races copied from V8)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import polars as pl
import pytest
from election_helpers import FakeContext, fixture_bytes, fixture_json

from datasites_core import fetch, publish
from datasites_core.contracts import load_contract
from datasites_core.fetch import Fetched
from datasites_core.layout import manifest_key, version_key
from datasites_core.store import get_json
from datasites_core.validate import validate_table
from datasites_election import medsl, medsl_senate

FIXTURE = "medsl_senate_excerpt.csv"
META = "dataverse_senate_metadata.json"
DATA_URL = "https://dataverse.harvard.edu/api/access/datafile/13887039?format=original"
META_KEY = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/PEJ5QU"
NOW = datetime(2026, 10, 24, 15, 37, tzinfo=timezone.utc)


@pytest.fixture
def senate_contract():
    return load_contract("electiondata/senate-results")


@pytest.fixture
def table():
    return medsl_senate.transform({"senate": fixture_bytes(FIXTURE)})


def for_excerpt(contract):
    """The real checks expect the whole file (3,900+ rows, a handful of no-count rows); the excerpt is 81 rows."""
    return dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=50, max_null_fraction={}))


def race(table, year, state, special=False, stage="GEN"):
    return table.filter(
        (pl.col("year") == year) & (pl.col("state_po") == state) & (pl.col("special") == special) & (pl.col("stage") == stage)
    ).sort("race_row")


def test_shape_matches_contract_and_validates(senate_contract, table):
    assert table.columns == senate_contract.columns
    assert len(table) == 81
    # A partial file is blocked by the real contract...
    report = validate_table(senate_contract, table, None)
    assert "81 rows, the contract requires at least 3900" in report.errors
    # ...and everything else about the excerpt passes.
    assert validate_table(for_excerpt(senate_contract), table, None).status == "pass"


def test_codes_are_made_consistent(table):
    assert set(table["stage"]) == {"GEN", "RUNOFF", "PRE"}
    assert set(table["mode"]) == {"TOTAL"}
    assert set(table["district"]) == {"STATEWIDE"}
    assert set(table["office"]) == {"US SENATE"}
    # 2022 Georgia's "GEN RUNOFF" and 2021's "runoff" mean the same thing.
    assert race(table, "2022", "GA", stage="RUNOFF")["candidate"].to_list() == ["HERSCHEL JUNIOR WALKER", "RAPHAEL WARNOCK"]
    assert len(race(table, "2021", "GA", stage="RUNOFF")) == 2
    assert len(race(table, "2021", "GA", special=True, stage="RUNOFF")) == 2
    # Georgia 1992: the November general that went to a runoff.
    assert race(table, "1992", "GA", stage="PRE")["candidatevotes"].to_list() == [69878, 1073282, 1108416, 11]


def test_no_count_marker_becomes_null(table):
    la = race(table, "1978", "LA").row(0, named=True)
    assert la["candidate"] == "J. BENNETT JOHNSTON JR."  # trailing space in the source is trimmed
    assert la["candidatevotes"] is None and la["totalvotes"] is None and la["candidate_total_votes"] is None
    # A real one-vote write-in line is not the marker.
    assert table.filter(pl.col("totalvotes") == 1).is_empty()


def test_key_is_unique_despite_repeated_lines(senate_contract, table):
    assert table.select(senate_contract.key).is_duplicated().sum() == 0
    ct = race(table, "2016", "CT")
    unnamed = ct.filter(pl.col("candidate").is_null())
    assert unnamed["writein"].to_list() == [True, True]
    assert unnamed["race_row"].to_list() == [6, 7]
    assert unnamed["candidatevotes"].to_list() == [26, 12]
    # Louisiana 2002: MEDSL lists the November primary and the December runoff under one race.
    landrieu = race(table, "2002", "LA").filter(pl.col("candidate") == "MARY L. LANDRIEU")
    assert landrieu["candidatevotes"].to_list() == [638654, 573347]
    assert landrieu["candidate_total_votes"].to_list() == [1212001, 1212001]


def test_regular_and_special_races_in_the_same_year(table):
    regular = race(table, "2024", "CA")
    special = race(table, "2024", "CA", special=True)
    assert regular["candidatevotes"].to_list() == [9036252, 6312594]
    assert special["candidatevotes"].to_list() == [8837051, 6204637]
    assert regular["totalvotes"].max() == 15348846


def test_fusion_lines_and_candidate_totals(table):
    ny = race(table, "2016", "NY")
    schumer = ny.filter(pl.col("candidate") == "CHARLES E. SCHUMER")
    assert sorted(schumer["party_detailed"].to_list()) == ["DEMOCRAT", "INDEPENDENCE", "WOMEN'S EQUALITY", "WORKING FAMILIES"]
    assert set(schumer["candidate_total_votes"]) == {4784220 + 241672 + 45402 + 150655}
    long = ny.filter(pl.col("candidate") == "WENDY LONG")
    assert set(long["candidate_total_votes"]) == {1723927 + 17813 + 267613}
    # Blank, void and scattered votes are their own rows and count towards MEDSL's totalvotes.
    assert {"BLANK VOTE", "VOID VOTE", "SCATTER"} <= set(ny["candidate"].to_list())
    # Unnamed write-ins get no candidate total.
    assert race(table, "2016", "CT").filter(pl.col("candidate").is_null())["candidate_total_votes"].null_count() == 2


def test_every_race_adds_up_to_its_total(table):
    sums = (
        table.filter(pl.col("totalvotes").is_not_null())
        .group_by(medsl_senate.RACE)
        .agg(pl.col("candidatevotes").sum().alias("sum"), pl.col("totalvotes").unique().alias("totals"))
    )
    assert sums["totals"].list.len().max() == 1
    assert (sums["sum"] == sums["totals"].list.first()).all()


def test_values_flags_and_versions(table):
    az = race(table, "2024", "AZ")
    assert az.select("candidate", "candidatevotes").rows() == [
        ("EDUARDO QUINTANA", 75868),
        ("KARI LAKE", 1595761),
        ("RUBEN GALLEGO", 1676335),
    ]
    gallego = az.row(2, named=True)
    assert gallego["totalvotes"] == 3347964
    assert gallego["unofficial"] is True
    assert gallego["version"] == "2025-11-20"  # '11/20/25' in the source
    assert gallego["state_id"] == "us-az" and gallego["state_fips"] == 4 and gallego["state_cen"] == 86 and gallego["state_ic"] == 61
    assert race(table, "1976", "AZ")["version"].unique().to_list() == ["2021-01-14"]  # '20210114'
    assert race(table, "2024", "CA", special=True)["version"].unique().to_list() == ["2024-12-12"]
    hi = race(table, "2022", "HI")
    over = hi.filter(pl.col("candidate") == "OVER VOTES").row(0, named=True)
    assert over["party_detailed"] is None and over["party_simplified"] is None
    assert hi.filter(pl.col("candidate") == "SCHATZ, BRIAN")["candidatevotes"].item() == 290894
    assert race(table, "1978", "CO").filter(pl.col("candidatevotes") == 2518)["candidate"].item() == "JOHN SHUE"


def test_changed_columns_or_codes_stop_the_run():
    body = fixture_bytes(FIXTURE)
    with pytest.raises(ValueError, match="columns changed"):
        medsl_senate.transform({"senate": body.replace(b"party_simplified", b"party_simple", 1)})
    with pytest.raises(ValueError, match="unexpected stage"):
        medsl_senate.transform({"senate": body.replace(b",statewide,gen,", b",statewide,primary,", 1)})
    with pytest.raises(ValueError, match="fractional"):
        medsl_senate.transform({"senate": body.replace(b",7310.0,", b",7310.5,", 1)})
    with pytest.raises(ValueError, match="unrecognized version"):
        medsl_senate.transform({"senate": body.replace(b",20210114,", b",2021-01-14,", 1)})


def test_resources_downloads_the_cited_version(senate_contract):
    ctx = FakeContext(senate_contract, {META_KEY: json.dumps(fixture_json(META)).encode()})
    assert medsl_senate.resources(ctx) == {"senate": DATA_URL}
    assert ctx.requests == [META_KEY]


def test_a_new_medsl_version_stops_the_run_until_reviewed(senate_contract):
    meta = fixture_json(META)
    meta["data"]["latestVersion"]["versionNumber"] = 9
    with pytest.raises(RuntimeError, match="published V9.0 but the contract cites V8.0"):
        medsl_senate.resources(FakeContext(senate_contract, {META_KEY: json.dumps(meta).encode()}))


def test_guestbook_datasets_are_refused():
    """The county presidential dataset (and the House one) put downloads behind a guestbook form."""
    meta = fixture_json("dataverse_countypres_metadata.json")
    citation = 'MIT Election Data and Science Lab, 2018, "County Presidential Election Returns 2000-2024", Harvard Dataverse, V20'
    with pytest.raises(RuntimeError, match="guestbook"):
        medsl.resolve_file(meta, "doi:10.7910/DVN/VOQCHQ", "countypres_2000-2024.csv", citation)


class Upstream:
    def __init__(self, body: bytes):
        self.body = body
        self.requests = []

    def __call__(self, url, etag, last_modified):
        assert url == DATA_URL
        self.requests.append(etag)
        tag = f'"{hash(self.body) & 0xFFFFFFFF:x}"'
        if etag == tag:
            return Fetched(url, 304, None, etag, last_modified)
        return Fetched(url, 200, self.body, tag, "Mon, 11 May 2026 16:09:14 GMT")


def run(contract, store, upstream, monkeypatch, at=NOW):
    meta = json.dumps(fixture_json(META)).encode()
    monkeypatch.setattr(fetch.CollectContext, "get", lambda self, url, **kw: meta)
    return publish.run_dataset(contract, store, fetcher=upstream, now=at)


def test_end_to_end_publish_then_unchanged_then_blocked(senate_contract, store, monkeypatch):
    lenient = for_excerpt(senate_contract)
    upstream = Upstream(fixture_bytes(FIXTURE))
    first = run(lenient, store, upstream, monkeypatch)
    assert first.outcome == "published", first.report.as_dict()
    doc, _ = get_json(store, version_key("electiondata", "senate-results", first.version))
    assert doc["coverage"] == {"start": "1976", "end": "2024"}
    assert doc["partition"] == "year" and doc["key"] == ["year", "state_po", "special", "stage", "race_row"]
    assert doc["source"]["license"] == "CC0-1.0" and doc["source"]["commercial_ok"] is True
    years = sorted(p for p in doc["files"] if p.startswith("table/") and p.endswith(".json"))
    assert years[:3] == ["table/1976.json", "table/1978.json", "table/1992.json"] and "table/all.json" in years
    assert doc["files"]["table/2024.json"]["rows"] == 3 + 4 + 4 + 6 + 3

    second = run(lenient, store, upstream, monkeypatch, at=NOW.replace(day=31))
    assert second.outcome == "unchanged" and upstream.requests[-1] is not None

    # A download cut off part way: the row count falls far below the published version, so it is quarantined.
    lines = upstream.body.split(b"\n")
    upstream.body = b"\n".join(lines[:20]) + b"\n"
    third = run(lenient, store, upstream, monkeypatch, at=NOW.replace(month=11, day=7))
    assert third.outcome == "blocked"
    assert any("row count fell" in e for e in third.report.errors)
    manifest, _ = get_json(store, manifest_key("electiondata", "senate-results"))
    assert manifest["current"] == first.version and third.version in manifest["quarantined"]
