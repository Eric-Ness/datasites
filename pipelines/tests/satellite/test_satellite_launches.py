import json

import pytest

from datasites_core.contracts import load_contract
from datasites_core.layout import blob_key, version_key
from datasites_core.publish import run_dataset
from datasites_core.store import get_json
from datasites_satellite import gcat_launches
from datasites_satellite.gcat import URLS
from satellite_helpers import NOW, FakeGCAT, gcat_raw, relaxed, rows_by

REF = "satellitedata/launches"


def launches(raw=None) -> dict[str, dict]:
    return rows_by(gcat_launches.transform(raw or gcat_raw(gcat_launches)), "launch_tag")


def test_columns_match_the_contract():
    assert gcat_launches.transform(gcat_raw(gcat_launches)).columns == load_contract(REF).columns


def test_sputnik():
    assert launches()["1957 ALP"] == {
        "launch_tag": "1957 ALP",
        "launch_class": "orbital",
        "launch_date": "1957-10-04",
        "launch_time": "1957-10-04T19:28:34Z",
        "launch_date_text": "1957 Oct  4 1928:34",
        "launch_date_precision": "second",
        "launch_jd": 2436116.31,
        "lv_type": "Sputnik 8K71PS",
        "lv_variant": None,
        "flight_id": "M1-PS",
        "flight": "PS-1",
        "mission": "PS-1",
        "flight_code": None,
        "platform": None,
        "launch_site": "NIIP-5",
        "launch_pad": "LC1",
        "ascent_site": None,
        "ascent_pad": None,
        "apogee_km": 938.0,
        "apogee_estimated": False,
        "orbital_payload_t": 0.084,
        "agency": "MVS",
        "agency_name": "Miinistry of the Armed Forces",  # sic, as GCAT spells it
        "launch_code": "OS",
        "outcome": "success",
        "success_percent": None,
        "category": "Sat LEO O 1",
    }


@pytest.mark.parametrize(
    "tag, date, time, precision",
    [
        ("1942-A01", "1942-06-13", "1942-06-13T10:52Z", "minute"),  # the first V-2 test: known to the minute, not the second
        ("1948-A01", None, None, "year"),
        ("1954-A01", None, None, "year (uncertain)"),
        ("1960-S05", None, None, "quarter (uncertain)"),
        ("1957-A41", "1957-05-22", None, "minute (uncertain)"),  # 0459-0501: the day is certain, the time is not
        ("1962-S613", "1962-07-09", None, "second (uncertain)"),
        ("2016-A109", None, None, None),  # 'DEc': not a valid vague date, so only the text is published
        ("2026-M02", None, None, "minute"),  # '2027 Jan  3' in a file regenerated 2026 Oct 24: a typo, not a date
    ],
)
def test_vague_launch_dates(tag, date, time, precision):
    row = launches()[tag]
    assert (row["launch_date"], row["launch_time"], row["launch_date_precision"]) == (date, time, precision)
    assert row["launch_date_text"] is not None


@pytest.mark.parametrize(
    "tag, launch_class, outcome, percent",
    [
        ("1957-F01", "orbital", "failure", None),  # Vanguard TV-3
        ("1989-092", "orbital", "success", 80),  # OS80: partial success
        ("1957-A41", "missile", "pad-explosion", None),
        ("1965-A245", "orbital", "pad-abort", None),  # Gemini 6 shutdown on the pad
        ("1959 MU", "deep-space", "success", None),  # Luna 1
        ("1960-S05", "high-altitude-sounding", "success", None),
        ("1971-000", "not-a-launch", None, None),  # a placeholder GCAT marks 'Not real'
    ],
)
def test_launch_codes(tag, launch_class, outcome, percent):
    row = launches()[tag]
    assert (row["launch_class"], row["outcome"], row["success_percent"]) == (launch_class, outcome, percent)


def test_undocumented_launch_code_stops_the_run():
    raw = gcat_raw(gcat_launches)
    assert b"\tOS80  \t" in raw["launch"]
    raw["launch"] = raw["launch"].replace(b"\tOS80  \t", b"\tQS    \t")
    with pytest.raises(ValueError, match="undocumented GCAT launch categories"):
        gcat_launches.transform(raw)


def test_end_to_end_publish_partitions_by_launch_class(store):
    contract = relaxed(REF, max_null_fraction={})  # the fixture is mostly vague-date edge cases
    upstream = FakeGCAT()
    result = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert result.outcome == "published", result.report and result.report.errors
    doc = get_json(store, version_key(contract.site, contract.id, result.version))[0]
    assert {p.split("/")[1].rsplit(".", 1)[0] for p in doc["files"] if p.startswith("table/")} == {
        "all", "orbital", "deep-space", "missile", "atmospheric", "suborbital", "high-altitude-sounding", "not-a-launch"}
    orbital = json.loads(store.get(blob_key(doc["files"]["table/orbital.json"]["blob"]))[0])
    assert orbital["meta"]["partition"] == {"launch_class": "orbital"}
    assert "1998-067" in {r["launch_tag"] for r in orbital["rows"]}
    assert doc["coverage"] == {"start": "1942-06-13", "end": "2026-10-23"}


def test_validation_blocks_a_launch_list_cut_short(store):
    contract = relaxed(REF, rows_min_ratio=0.99, max_null_fraction={})
    upstream = FakeGCAT()
    assert run_dataset(contract, store, fetcher=upstream, now=NOW).outcome == "published"
    url = URLS["launch"]
    lines = upstream.bodies[url].split(b"\n")
    upstream.bodies[url] = b"\n".join(lines[: len(lines) // 2]) + b"\n"
    blocked = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert blocked.outcome == "blocked" and any("row count fell" in e for e in blocked.report.errors)
