"""housing-costs: ACS 5-year rent, home value, rent burden and tenure.

Fixtures are verbatim Census Data API responses, captured 2026-11-26, for
https://api.census.gov/data/<year>/acs/acs5?get=<census_acs_housing.variables()>&for=... (the key is not in them):
- acs5_2024_housing_counties_ca.json: for=county:075,041,003&in=state:06 (San Francisco, Marin, Alpine);
- acs5_2024_housing_states.json: for=state:06,56,72; acs5_2024_housing_us.json: for=us:1;
- acs5_2009_housing_counties_annotated.json: the rows of for=county:* (2009) whose medians carry an annotation.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime, timezone

import httpx
import polars as pl
import pytest
from housing_helpers import FIXTURES

from datasites_core.contracts import load_contract
from datasites_core.validate import validate
from datasites_housing import census_acs_housing as acs

COUNTIES, STATES, US = "acs5_2024_housing_counties_ca.json", "acs5_2024_housing_states.json", "acs5_2024_housing_us.json"
ANNOTATED_2009 = "acs5_2009_housing_counties_annotated.json"
FAKE_KEY = "not-a-real-census-key"


def body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def raw_2024() -> dict[str, bytes]:
    return {"2024-county": body(COUNTIES), "2024-state": body(STATES), "2024-us": body(US)}


def rows(table: pl.DataFrame, location: str) -> dict[str, dict]:
    return {r["series"]: r for r in table.filter(pl.col("location") == location).iter_rows(named=True)}


def test_medians_come_straight_from_census():
    sf = rows(acs.transform(raw_2024()), "us-county-06075")
    assert (sf["median-gross-rent"]["value"], sf["median-gross-rent"]["margin_of_error"]) == (2476, 30)
    assert (sf["median-home-value"]["value"], sf["median-home-value"]["margin_of_error"]) == (1394500, 20642)
    assert sf["median-gross-rent"]["location_name"] == "San Francisco County, California"


def test_shares_and_their_margins_follow_the_census_formula():
    """Recomputed here from the raw counts, independently of the pipeline's expressions."""
    table = json.loads(body(COUNTIES))
    header = table[0]
    sf = next(r for r in table[1:] if r[-1] == "075")
    g = lambda v: float(sf[header.index(v)])  # noqa: E731
    burdened = sum(g(f"B25070_{n:03d}E") for n in (7, 8, 9, 10))
    burdened_moe = math.sqrt(sum(g(f"B25070_{n:03d}M") ** 2 for n in (7, 8, 9, 10)))
    computed = g("B25070_001E") - g("B25070_011E")
    computed_moe = math.sqrt(g("B25070_001M") ** 2 + g("B25070_011M") ** 2)
    p = burdened / computed
    expected = (round(100 * p, 1), round(100 * math.sqrt(burdened_moe**2 - p**2 * computed_moe**2) / computed, 1))

    out = rows(acs.transform(raw_2024()), "us-county-06075")
    assert (out["rent-burdened-share"]["value"], out["rent-burdened-share"]["margin_of_error"]) == expected == (39.6, 1.0)
    assert out["owner-occupied-share"]["value"] == round(100 * g("B25003_002E") / g("B25003_001E"), 1) == 38.2


def test_national_and_state_rows():
    table = acs.transform(raw_2024())
    usa = rows(table, "usa")
    assert {s: r["value"] for s, r in usa.items()} == {
        "median-gross-rent": 1413,
        "median-home-value": 332700,
        "rent-burdened-share": 51.1,
        "owner-occupied-share": 65.2,
    }
    assert set(table.filter(pl.col("location").str.starts_with("us-") & ~pl.col("location").str.starts_with("us-county"))["location"]) == {
        "us-ca",
        "us-wy",
        "us-pr",
    }


def test_no_value_and_open_ended_medians_keep_census_annotations():
    table = acs.transform({"2009-county": body(ANNOTATED_2009)})
    loving, kalawao, nantucket = rows(table, "us-county-48301"), rows(table, "us-county-15005"), rows(table, "us-county-25019")
    assert loving["median-gross-rent"]["value"] is None and loving["median-gross-rent"]["estimate_annotation"] == "-"
    assert kalawao["median-gross-rent"]["value"] == 2001 and kalawao["median-gross-rent"]["estimate_annotation"] == "2,000+"
    assert kalawao["median-home-value"]["value"] is None and kalawao["median-home-value"]["estimate_annotation"] == "-"
    assert nantucket["median-home-value"]["value"] == 1000001 and nantucket["median-home-value"]["estimate_annotation"] == "1,000,000+"
    assert set(table["date"]) == {"2009"}


def test_a_share_with_no_renters_is_null_not_zero():
    table = json.loads(body(COUNTIES))
    header = table[0]
    row = list(table[1])
    for var in ("B25070_001E", "B25070_011E"):
        row[header.index(var)] = "0"
    out = rows(acs.read_response(json.dumps([header, row]).encode(), "2024"), "us-county-06" + row[-1])
    assert out["rent-burdened-share"]["value"] is None and out["rent-burdened-share"]["margin_of_error"] is None


def test_a_response_without_a_variable_is_rejected():
    table = json.loads(body(US))
    i = table[0].index("B25077_001EA")
    broken = [[v for j, v in enumerate(r) if j != i] for r in table]
    with pytest.raises(ValueError, match="missing"):
        acs.read_response(json.dumps(broken).encode(), "2024")


class FakeCensus:
    """ctx for collect(): releases up to 2024, responses from the fixtures, and the key it was given."""

    now = datetime(2026, 11, 26, tzinfo=timezone.utc)

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    def env(self, name):
        assert name == "CENSUS_API_KEY"
        return FAKE_KEY

    def get(self, url, params=None):
        self.calls.append((url, params))
        if url.endswith(".json"):
            if "/2025/" in url:
                raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))
            return b"{}"
        level = params["for"].split(":")[0]
        return body({"county": COUNTIES, "state": STATES, "us": US}[level])


def test_collect_asks_for_every_release_and_level_with_the_key():
    ctx = FakeCensus()
    raw = acs.collect(ctx)
    assert sorted(raw) == sorted(f"{y}-{level}" for y in range(2009, 2025) for level in ("county", "state", "us"))
    data_calls = [p for _, p in ctx.calls if p]
    assert all(p["key"] == FAKE_KEY and p["get"] == ",".join(acs.variables()) for p in data_calls)
    assert len(acs.variables()) <= 50  # the API's limit per request


def test_a_rejected_key_says_so_without_leaking_it():
    class RejectedKey(FakeCensus):
        def get(self, url, params=None):
            if params:  # Census answers a bad key with an HTML page, not JSON
                return b"<html>Invalid Key</html>"
            return super().get(url, params)

    with pytest.raises(RuntimeError, match="CENSUS_API_KEY valid") as err:
        acs.collect(RejectedKey())
    assert FAKE_KEY not in str(err.value)


def test_output_passes_the_contract():
    contract = load_contract("housingdata/housing-costs")
    # The excerpt is small and picked for its no-value counties, so its row count and null share are not
    # the real table's; the full run checks those (min_rows, max_null_fraction).
    excerpt = dataclasses.replace(contract, checks=dataclasses.replace(contract.checks, min_rows=40, max_null_fraction={}))
    table = acs.transform({**raw_2024(), "2009-county": body(ANNOTATED_2009)})
    report = validate(excerpt, table.drop("location_name"), None)  # the engine moves names into the location index
    assert report.status == "pass", report.as_dict()
