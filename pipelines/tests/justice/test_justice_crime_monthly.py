"""justicedata/crime-monthly: FBI CDE summarized offenses by state.

The fixtures are verbatim CDE answers for /summarized/state/{XX}/{V|P}?from=01-2024&to=03-2024, captured
2026-11-06 from the backend the CDE API on api.data.gov forwards to (JSON has no room for a comment header).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from datasites_core.contracts import load_contract
from datasites_core.validate import validate
from datasites_justice import cde, crime_monthly

FIXTURES = Path(__file__).parent / "fixtures"


def raw(*names: str) -> dict[str, bytes]:
    return {name: (FIXTURES / f"summarized_{name}.json").read_bytes() for name in names}


@pytest.fixture
def table():
    return crime_monthly.transform(raw("MN-V", "MN-P", "DC-V"))


def at(table, series, location, date):
    rows = table.filter(
        (pl.col("series") == series)
        & (pl.col("location") == location)
        & (pl.col("date") == date)
    )
    assert len(rows) == 1, (series, location, date)
    return rows.row(0, named=True)


def test_shape_and_validation(table):
    contract = load_contract("justicedata/crime-monthly")
    assert table.columns == contract.columns
    assert set(table["series"]) == set(contract.series)
    lenient = dataclasses.replace(
        contract,
        checks=dataclasses.replace(contract.checks, min_rows=5, max_null_fraction={}),
    )
    assert validate(lenient, table, None).status == "pass"
    # 3 answers x 3 months, plus the national rows once per series (not once per answer)
    assert len(table) == 9 + 3 + 3


def test_state_values(table):
    mn = at(table, "violent-crime", "us-mn", "2024-01")
    assert (
        mn["offenses"],
        mn["clearances"],
        mn["offense_rate"],
        mn["clearance_rate"],
    ) == (1169, 547, 20.2, 9.45)
    assert (mn["coverage_pct"], mn["population"], mn["covered_population"]) == (
        99.89,
        5793151,
        5787032,
    )
    assert at(table, "property-crime", "us-mn", "2024-03")["offenses"] == 6840
    dc = at(table, "violent-crime", "us-dc", "2024-02")
    assert (dc["offenses"], dc["clearances"], dc["coverage_pct"]) == (515, 267, 100.0)


def test_national_rows_have_rates_but_no_counts(table):
    us = at(table, "violent-crime", "usa", "2024-03")
    assert us["offenses"] is None and us["clearances"] is None
    assert (us["offense_rate"], us["coverage_pct"], us["population"]) == (
        29.32,
        96.08,
        343551384,
    )
    assert (
        at(table, "property-crime", "usa", "2024-03")["offense_rate"]
        != us["offense_rate"]
    )


def test_national_figures_must_agree_between_answers():
    files = raw("MN-V", "DC-V")
    doc = json.loads(files["DC-V"])
    doc["offenses"]["rates"]["United States Offenses"]["02-2024"] = 99.0
    files["DC-V"] = json.dumps(doc).encode()
    with pytest.raises(ValueError, match="differ between states"):
        crime_monthly.transform(files)


def test_unexpected_answers_are_refused():
    with pytest.raises(ValueError, match="unexpected raw file"):
        crime_monthly.transform({"PR-V": raw("MN-V")["MN-V"]})
    doc = json.loads(raw("MN-V")["MN-V"])
    del doc["offenses"]["rates"]["Minnesota Clearances"]
    with pytest.raises(ValueError, match="Minnesota Clearances"):
        crime_monthly.transform({"MN-V": json.dumps(doc).encode()})


class FakeCtx:
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)

    def __init__(self):
        self.calls = []

    def env(self, name):
        assert name == cde.KEY_ENV
        return "test-key"

    def get(self, url, *, params=None, headers=None, timeout=120):
        self.calls.append((url, params, headers))
        return raw("MN-V")["MN-V"]


def test_collect_asks_for_every_state_with_the_key_in_a_header():
    ctx = FakeCtx()
    files = crime_monthly.collect(ctx)
    assert len(files) == len(ctx.calls) == 51 * 2
    url, params, headers = ctx.calls[0]
    assert url == f"{cde.API}/summarized/state/AK/V"
    assert params == {"from": "01-2018", "to": "11-2026"}
    assert headers == {"X-Api-Key": "test-key"}
    assert all(
        "test-key" not in u and "test-key" not in str(p) for u, p, _ in ctx.calls
    )


def test_api_errors_are_raised():
    ctx = FakeCtx()
    ctx.get = (
        lambda *a,
        **k: b'{"error":{"code":"OVER_RATE_LIMIT","message":"You have exceeded your rate limit."}}'
    )
    with pytest.raises(RuntimeError, match="OVER_RATE_LIMIT"):
        cde.get(ctx, "summarized/state/MN/V", {})
