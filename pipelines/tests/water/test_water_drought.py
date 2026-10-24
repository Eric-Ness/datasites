import json
from urllib.parse import parse_qs, urlsplit

import pytest

from datasites_core.contracts import load_contract
from datasites_core.publish import run_dataset
from datasites_water import usdm_county
from water_helpers import NOW, FakeCtx, FakeUSDM, fixture, published_file, relaxed, version_doc


def usdm_raw(**replace) -> dict[str, bytes]:
    raw = {f"counties-{s}": fixture(f"usdm_counties_{s}.csv") for s in ("ct", "dc", "md")}
    for name, (old, new) in replace.items():
        assert old in raw[name]
        raw[name] = raw[name].replace(old, new)
    return raw


def rows(raw=None) -> dict[tuple[str, str], dict]:
    table = usdm_county.transform(raw or usdm_raw())
    return {(r["location"], r["date"]): r for r in table.iter_rows(named=True)}


def test_one_request_per_state_with_counties():
    states = usdm_county.states()
    assert len(states) == 52 and {"DC", "PR", "TX", "AK", "HI"} <= set(states)
    assert not {"GU", "VI", "AS", "MP", "UM"} & set(states)  # USDM publishes no county statistics for these


def test_resources_ask_for_categorical_statistics_over_the_window():
    urls = usdm_county.resources(FakeCtx())
    assert len(urls) == 52
    query = parse_qs(urlsplit(urls["counties-md"]).query)
    assert query == {"aoi": ["MD"], "startdate": ["9/20/2025"], "enddate": ["10/24/2026"], "statisticsType": ["2"]}


def test_columns_and_a_known_county_week():
    contract = load_contract("waterdata/drought-county")
    table = usdm_county.transform(usdm_raw())
    assert table.columns == contract.columns
    # Montgomery County, MD for the map valid 2026-10-20.
    assert rows()[("us-county-24031", "2026-10-20")] == {
        "series": "area", "location": "us-county-24031", "date": "2026-10-20",
        "none": 0.0, "d0": 97.9, "d1": 2.1, "d2": 0.0, "d3": 0.0, "d4": 0.0,
    }
    assert rows()[("us-county-11001", "2026-10-20")]["d0"] == 100.0


def test_categories_are_exclusive_and_add_up_to_100():
    table = usdm_county.transform(usdm_raw())
    total = table["none"] + table["d0"] + table["d1"] + table["d2"] + table["d3"] + table["d4"]
    assert ((total - 100).abs() <= 0.02).all()
    # 24 Maryland counties (with Baltimore city), 9 Connecticut planning regions, DC; 9 weekly maps.
    assert table["location"].n_unique() == 34 and table["date"].n_unique() == 9
    assert {"us-county-09110", "us-county-09190"} <= set(table["location"])


def test_cumulative_statistics_are_refused():
    # statisticsType=1 ("traditional") is cumulative: its D1 column means D1-D4. Mixing it in would be wrong.
    raw = usdm_raw(**{"counties-dc": (b",2\r\n", b",1\r\n")})
    with pytest.raises(ValueError, match="expected categorical"):
        usdm_county.transform(raw)


def test_an_empty_state_response_is_an_error():
    raw = usdm_raw()
    raw["counties-md"] = b""
    with pytest.raises(ValueError, match="no county statistics"):
        usdm_county.transform(raw)


def test_end_to_end_publishes_and_blocks_bad_input(store, monkeypatch):
    contract = relaxed("waterdata/drought-county", min_rows=100)
    monkeypatch.setattr(usdm_county, "states", lambda: ["CT", "DC", "MD"])
    upstream = FakeUSDM()
    result = run_dataset(contract, store, fetcher=upstream, now=NOW)
    assert result.outcome == "published", result.report.errors
    assert len(upstream.urls) == 3
    doc = version_doc(store, contract, result.version)
    assert doc["coverage"] == {"start": "2026-08-25", "end": "2026-10-20"}
    assert doc["locations"]["us-county-24031"] == "Montgomery County"
    assert doc["source"]["attribution"].endswith("Map courtesy of NDMC.")
    montgomery = json.loads(published_file(store, doc, "area/us-county-24031.json"))
    assert montgomery["data"][-1] == {"date": "2026-10-20", "none": 0.0, "d0": 97.9, "d1": 2.1, "d2": 0.0, "d3": 0.0, "d4": 0.0}
    assert montgomery["meta"]["units"]["d4"] == "percent"

    # The source switching Connecticut back to its pre-2022 counties: ids the registry doesn't have.
    legacy = run_dataset(contract, store, fetcher=FakeUSDM(replace=(b",09110,", b",09001,")), now=NOW.replace(day=25))
    assert legacy.outcome == "blocked"
    assert any("not allowed" in e and "us-county-09001" in e for e in legacy.report.errors)

    # A corrupted percentage.
    corrupt = run_dataset(contract, store, fetcher=FakeUSDM(replace=(b",97.90,2.10,", b",979.0,2.10,")), now=NOW.replace(day=26))
    assert corrupt.outcome == "blocked"
    assert any(e.startswith("d0:") and "outside [0.0, 100.0]" in e for e in corrupt.report.errors)
