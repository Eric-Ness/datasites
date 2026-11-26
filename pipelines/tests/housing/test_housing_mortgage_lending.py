"""mortgage-lending: HMDA snapshot loan-level records counted by county, state and nation.

The loan-level fixtures are written here: rows in the snapshot's own columns (the header of
2024_public_lar_csv.csv, first six used columns kept), zipped like the real file.
"""

from __future__ import annotations

import dataclasses
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from datasites_core.contracts import load_contract
from datasites_core.validate import validate
from datasites_housing import hmda_lending as hmda

HEADER = "activity_year,lei,state_code,county_code,action_taken,loan_purpose,loan_amount,interest_rate"
# year, state, county, action, purpose, amount
LOANS = [
    ("2024", "MD", "24031", "1", "1", "505000"),
    ("2024", "MD", "24031", "1", "1", "305000"),
    ("2024", "MD", "24031", "1", "1", "405000"),
    ("2024", "MD", "24031", "3", "1", "255000"),  # denied
    ("2024", "MD", "24031", "4", "1", "255000"),  # withdrawn: an application, not decided
    ("2024", "MD", "24031", "6", "1", "999000"),  # purchased loan: not counted
    ("2024", "MD", "24031", "7", "1", "999000"),  # preapproval denied: not counted
    ("2024", "MD", "24033", "1", "31", "205000"),
    ("2024", "MD", "NA", "1", "1", "105000"),  # no county: counts for Maryland and the US
    ("2024", "VA", "51059", "2", "32", "305000"),  # approved, not accepted
    ("2024", "VA", "51059", "1", "5", "305000"),  # purpose not applicable: not counted
]


def lar_zip(path: Path, loans=LOANS, year="2024") -> Path:
    rows = [HEADER] + [f"{y},LEI{i},{st},{co},{a},{p},{amt}," for i, (y, st, co, a, p, amt) in enumerate(loans)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{year}_public_lar_csv.csv", "\n".join(rows) + "\n")
    return path


@pytest.fixture
def year_2024(tmp_path):
    return hmda.read_zip(lar_zip(tmp_path / "2024.zip"), "2024", hmda.SNAPSHOT.format(year=2024), '"etag-1"', tmp_path)


def row(table, location, purpose):
    found = table.filter((pl.col("location") == location) & (pl.col("loan_purpose") == purpose))
    assert len(found) == 1, (location, purpose)
    return found.row(0, named=True)


def test_counts_by_county(year_2024):
    mc = row(year_2024, "us-county-24031", "home-purchase")
    assert (mc["applications"], mc["originations"], mc["denials"]) == (5, 3, 1)
    assert mc["denial_rate"] == 25.0  # 1 denial of 4 decided (3 originated + 1 denied; the withdrawal isn't decided)
    assert (mc["originated_amount"], mc["median_originated_amount"]) == (1215000, 405000)
    assert mc["source_etag"] == '"etag-1"' and mc["source_url"].endswith("/2024/2024_public_lar_csv.zip")


def test_states_and_nation_include_records_without_a_county(year_2024):
    md = row(year_2024, "us-md", "home-purchase")
    assert (md["applications"], md["originations"]) == (6, 4)
    assert row(year_2024, "usa", "home-purchase")["originations"] == 4
    assert row(year_2024, "usa", "refinance")["originations"] == 1
    va = row(year_2024, "us-county-51059", "cash-out-refinance")
    assert (va["applications"], va["originations"], va["denial_rate"], va["median_originated_amount"]) == (1, 0, 0.0, None)
    assert va["originated_amount"] == 0


def test_purchased_loans_preapprovals_and_not_applicable_purposes_are_left_out(year_2024):
    assert year_2024["applications"].filter(year_2024["location"] == "usa").sum() == 8
    assert set(year_2024["loan_purpose"]) <= set(hmda.PURPOSES.values())


def test_a_snapshot_with_another_year_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="activity years"):
        hmda.read_zip(lar_zip(tmp_path / "x.zip"), "2023", "u", "e", tmp_path)


def test_raw_round_trips(year_2024):
    assert hmda.from_bytes(hmda.to_bytes(year_2024)).equals(year_2024.select(hmda.COLUMNS))


class FakeContext:
    now = datetime(2026, 11, 26, tzinfo=timezone.utc)

    def __init__(self, previous: dict[str, bytes] | None = None):
        self.blobs = previous or {}

    def previous_files(self):
        return {f"table/{year}.csv": {"blob": f"hash-{year}"} for year in self.blobs} | {"table/2024.json": {"blob": "json"}}

    def read_blob(self, sha):
        return self.blobs.get(sha.removeprefix("hash-"))


@pytest.fixture
def snapshots(monkeypatch, tmp_path):
    """Snapshots for 2018-2025 (2026 not out), served from zips written here; records each download."""
    etags = {year: f'"etag-{year}"' for year in range(2018, 2026)}
    downloads: list[int] = []

    def probe(url):
        year = int(url.rsplit("/", 1)[1][:4])
        return etags.get(year)

    def download(url, dest):
        year = int(url.rsplit("/", 1)[1][:4])
        downloads.append(year)
        lar_zip(dest, [(str(year), *loan[1:]) for loan in LOANS], str(year))
        return etags[year]

    monkeypatch.setattr(hmda, "probe", probe)
    monkeypatch.setattr(hmda, "download", download)
    return etags, downloads


def test_first_run_downloads_every_year(snapshots):
    _, downloads = snapshots
    raw = hmda.collect(FakeContext())
    assert sorted(raw) == [str(y) for y in range(2018, 2026)] and downloads == list(range(2018, 2026))
    table = hmda.transform(raw)
    assert sorted(table["year"].unique()) == [str(y) for y in range(2018, 2026)]


def test_an_unchanged_snapshot_is_not_downloaded_again(snapshots):
    etags, downloads = snapshots
    first = hmda.collect(FakeContext())
    downloads.clear()
    etags[2025] = '"etag-2025-revised"'  # CFPB re-uploaded 2025 only
    again = hmda.collect(FakeContext(previous=first))
    assert downloads == [2025]
    assert {y: again[y] == first[y] for y in again} == {str(y): y != 2025 for y in range(2018, 2026)}
    assert hmda.from_bytes(again["2025"])["source_etag"].unique().to_list() == ['"etag-2025-revised"']


def test_a_missing_older_year_stops_the_run(snapshots):
    etags, _ = snapshots
    del etags[2020]
    with pytest.raises(RuntimeError, match="2020 snapshot is missing"):
        hmda.collect(FakeContext())


def test_output_passes_the_contract(snapshots):
    contract = load_contract("housingdata/mortgage-lending")
    excerpt = dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=10, max_null_fraction={}))
    report = validate(excerpt, hmda.transform(hmda.collect(FakeContext())), None)
    assert report.status == "pass", report.as_dict()


def test_csv_reader_needs_no_type_guessing(tmp_path):
    """Leading zeros in county codes and 'NA' must survive: every column is read as text first."""
    table = hmda.read_zip(lar_zip(tmp_path / "z.zip", [("2024", "AL", "01001", "1", "1", "105000")]), "2024", "u", "e", tmp_path)
    assert "us-county-01001" in table["location"].to_list()
