import json
from datetime import date, timedelta

import pytest

from datasites_core.contracts import load_contract
from datasites_core.publish import run_dataset
from datasites_water import usgs, usgs_daily
from water_helpers import NOW, FakeCtx, FakeUSGS, csv_rows, published_file, relaxed, usgs_raw, version_doc


def flows(raw=None) -> dict[tuple[str, str], dict]:
    table = usgs_daily.transform(raw or usgs_raw())
    return {(row["location"], row["date"]): row for row in table.iter_rows(named=True)}


def test_columns_match_the_contract():
    contract = load_contract("waterdata/streamflow-daily")
    assert usgs_daily.transform(usgs_raw()).columns == [*contract.columns[:2], "location_name", *contract.columns[2:]]


def test_approved_then_provisional_at_the_potomac():
    rows = flows()
    assert rows[("usgs-01646500", "2026-04-06")] == {
        "series": "discharge",
        "location": "usgs-01646500",
        "location_name": "POTOMAC RIVER NEAR WASH, DC LITTLE FALLS PUMP STA",
        "date": "2026-04-06",
        "value": 7520.0,
        "provisional": False,
        "estimated": False,
        "ice": False,
        "qualifiers": None,
    }
    assert rows[("usgs-01646500", "2026-04-07")]["value"] == 7920.0
    assert rows[("usgs-01646500", "2026-04-07")]["provisional"] is True


def test_ice_affected_days_with_and_without_a_value():
    rows = flows()
    no_value = rows[("usgs-01634500", "2026-01-16")]  # Cedar Creek: ICE, no daily mean published
    assert (no_value["value"], no_value["ice"], no_value["estimated"], no_value["qualifiers"]) == (None, True, False, "ICE")
    assert rows[("usgs-01634500", "2026-01-15")]["value"] == 17.8 and rows[("usgs-01634500", "2026-01-15")]["ice"] is False
    estimated = rows[("usgs-01360640", "2026-01-14")]  # Valatie Kill: an estimated ice-affected value
    assert (estimated["value"], estimated["ice"], estimated["estimated"], estimated["qualifiers"]) == (16.1, True, True, "ESTIMATED,ICE")
    assert rows[("usgs-01360640", "2026-01-13")]["estimated"] is False and rows[("usgs-01360640", "2026-01-13")]["ice"] is True


def test_missing_days_stay_missing():
    rows = flows()
    ohio = sorted(d for loc, d in rows if loc == "usgs-03294500" and d.startswith("2026-01"))
    assert ohio == ["2026-01-12", "2026-01-14", "2026-01-15", "2026-01-16"]  # no record for the 13th
    guam = sorted(d for loc, d in rows if loc == "usgs-16809600" and d.startswith("2026-01"))
    assert guam == ["2026-01-12", "2026-01-13", "2026-01-16"]
    # The Red River at Emerson stops reporting at the end of December: nothing is filled in for January.
    assert not any(loc == "usgs-05102500" and d.startswith("2026") for loc, d in rows)


def test_negative_flow_and_other_qualifiers_are_kept():
    rows = flows()
    assert rows[("usgs-02290765", "2025-09-27")]["value"] == -190.0
    emerson = rows[("usgs-05102500", "2025-09-30")]
    assert (emerson["value"], emerson["qualifiers"], emerson["ice"], emerson["estimated"]) == (2540.0, "OTHERAGENCY", False, False)


def test_one_series_per_gage_and_no_springs_or_other_agencies():
    table = usgs_daily.transform(usgs_raw())
    assert not table.select("location", "date").is_duplicated().any()
    # Codorus Creek: the main series (51.0), not the one adjusted for diversions (74.8).
    assert flows()[("usgs-01574500", "2025-09-26")]["value"] == 51.0
    assert set(table["location"]) == {
        "usgs-01360640", "usgs-01574500", "usgs-01634500", "usgs-01646500",
        "usgs-02290765", "usgs-03294500", "usgs-05102500", "usgs-16809600",
    }


def test_rows_outside_the_window_are_dropped():
    raw = usgs_raw(start="2025-09-28")
    dates = {d for _, d in flows(raw)}
    assert min(dates) == "2025-09-28"


def test_unparseable_values_fail_loudly():
    raw = usgs_raw()
    raw["daily-2026-04-03"] = raw["daily-2026-04-03"].replace(b"2026-04-07,7920,", b"2026-04-07,7920 cfs,", 1)
    with pytest.raises(Exception, match="7920 cfs"):
        usgs_daily.transform(raw)


def test_blocks_are_aligned_and_cover_the_window_once():
    start, end = date(2025, 9, 20), date(2026, 10, 24)
    blocks = usgs_daily.blocks(start, end)
    assert blocks[0][0] == start and blocks[-1][1] == end
    for (_, last), (first, _) in zip(blocks, blocks[1:]):
        assert first == last + timedelta(days=1)
    # Every block but the first starts on a fixed boundary, so tomorrow's requests repeat today's.
    assert all((first - usgs_daily.EPOCH).days % usgs_daily.BLOCK_DAYS == 0 for first, _ in blocks[1:])
    assert all((last - first).days < usgs_daily.BLOCK_DAYS for first, last in blocks)
    tomorrow = usgs_daily.blocks(start + timedelta(days=1), end + timedelta(days=1))
    assert set(blocks[1:-1]) <= set(tomorrow)
    assert 80 <= len(blocks) <= 82


def test_collect_fetches_the_window_in_blocks(monkeypatch):
    fake = FakeUSGS().install(monkeypatch)
    raw = usgs_daily.collect(FakeCtx())
    daily = [p for u, p, _ in fake.requests if u.endswith("/daily/items")]
    assert len(daily) == len(usgs_daily.blocks(date(2025, 9, 20), date(2026, 10, 24)))
    assert all(p["properties"] == "time_series_id,time,value,approval_status,qualifier" for p in daily)
    assert all(p["parameter_code"] == "00060" and p["statistic_id"] == "00003" for p in daily)
    assert json.loads(raw["query"])["block_days"] == 5
    assert usgs_daily.transform(raw).sort("location", "date").equals(usgs_daily.transform(usgs_raw()).sort("location", "date"))


def test_a_block_that_fills_a_page_is_split(monkeypatch):
    monkeypatch.setattr(usgs, "PAGE_LIMIT", 13)  # the daily fixtures hold ~40 rows per 5 days; the series fixture has 12 rows
    FakeUSGS().install(monkeypatch)
    raw = usgs_daily.collect(FakeCtx())
    january = sorted(n for n in raw if n.startswith("daily-2026-01"))
    assert len(january) > 2  # the blocks holding 2026-01-12..16 were split
    assert all(len(usgs.read_csv(raw[n], usgs_daily.DAILY_COLUMNS)) < 13 for n in raw if n.startswith("daily-"))
    assert usgs_daily.transform(raw).sort("location", "date").equals(usgs_daily.transform(usgs_raw()).sort("location", "date"))


def test_a_single_day_filling_a_page_is_an_error(monkeypatch):
    monkeypatch.setattr(usgs, "PAGE_LIMIT", 13)
    with pytest.raises(RuntimeError, match="alone fill"):
        FakeUSGS(repeat_daily=2).install(monkeypatch)
        usgs_daily.collect(FakeCtx())  # every row twice: ~20 rows for a single day


def test_end_to_end_publish_then_block_overlapping_blocks(store, monkeypatch):
    contract = relaxed("waterdata/streamflow-daily", min_rows=50)
    FakeUSGS().install(monkeypatch)
    result = run_dataset(contract, store, now=NOW)
    assert result.outcome == "published", result.report.errors
    doc = version_doc(store, contract, result.version)
    assert doc["locations"]["usgs-01646500"] == "POTOMAC RIVER NEAR WASH, DC LITTLE FALLS PUMP STA"
    assert doc["coverage"] == {"start": "2025-09-26", "end": "2026-04-09"}
    potomac = json.loads(published_file(store, doc, "discharge/usgs-01646500.json"))
    assert potomac["meta"]["units"] == {"value": "ft^3/s"}
    assert potomac["meta"]["attribution"] == "Streamflow data courtesy of the U.S. Geological Survey."
    assert {"date": "2026-04-07", "value": 7920.0, "provisional": True, "estimated": False, "ice": False, "qualifiers": None} in potomac["data"]
    cedar = csv_rows(published_file(store, doc, "discharge/usgs-01634500.csv"))
    assert {"date": "2026-01-16", "value": "", "provisional": "true", "estimated": "false", "ice": "true", "qualifiers": "ICE"} in cedar
    # Only the small metadata responses are archived; the daily blocks are over the 1 MB cap in production.
    assert all(r["key"] for r in doc["source"]["raw"] if not r["name"].startswith("daily-"))

    # A server that repeats rows (overlapping pages) would double-count days: validation blocks it.
    FakeUSGS(repeat_daily=2).install(monkeypatch)
    blocked = run_dataset(contract, store, now=NOW.replace(day=25))
    assert blocked.outcome == "blocked"
    assert any("share a series/location/date key" in e for e in blocked.report.errors)
