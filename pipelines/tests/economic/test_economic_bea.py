import json

import httpx
import polars as pl
import pytest

from datasites_core.validate import validate
from datasites_economic import bea_cainc1
from economic_helpers import FakeContext, contract, fixture

DE_2014 = "bea_getdata_cainc1_line3_de_2014.json"  # GetData Example 1 of BEA's API user guide
PLACEHOLDER_KEY = "Your-36Character-Key"  # the guide's stand-in for a UserID, echoed in its examples


def test_reads_a_documented_cainc1_response():
    df = bea_cainc1.read_results(fixture(DE_2014), "3")
    assert df.to_dicts() == [
        {"fips": "10000", "geo_name": "Delaware", "date": "2014", "per_capita_personal_income": 45359.0},
        {"fips": "10001", "geo_name": "Kent", "date": "2014", "per_capita_personal_income": 37285.0},
        {"fips": "10003", "geo_name": "New Castle", "date": "2014", "per_capita_personal_income": 48080.0},
        {"fips": "10005", "geo_name": "Sussex", "date": "2014", "per_capita_personal_income": 44738.0},
    ]


def test_another_table_or_unit_is_refused():
    # GetData Example 2 is SAINC1 (the state table), not CAINC1: same shape, different statistic.
    with pytest.raises(ValueError, match="expected Dollars .*got SAINC1-3"):
        bea_cainc1.read_results(fixture("bea_getdata_sainc1_line3_state_2013.json"), "3")
    # Line 1 must be thousands of dollars; a per capita result under that line is caught by its unit.
    with pytest.raises(ValueError, match="expected Thousands of dollars"):
        bea_cainc1.read_results(fixture(DE_2014), "1")


def test_geography_codes():
    doc = json.loads(fixture("bea_getdata_sainc1_line3_state_2013.json"))
    fips = [r["GeoFips"] for r in doc["BEAAPI"]["Results"]["Data"]] + ["10001", "51901", "15901", "09001"]
    ids = pl.DataFrame({"fips": fips}).select(bea_cainc1.location_ids(pl.col("fips")).alias("id"))["id"].to_list()
    assert dict(zip(fips, ids)) == {
        "00000": "usa",
        "01000": "us-al",
        "02000": "us-ak",
        "04000": "us-az",
        "97000": None,  # BEA region (Rocky Mountain): left out
        "98000": None,  # BEA region (Far West)
        "10001": "us-county-10001",
        "51901": "bea-51901",  # Albemarle + Charlottesville, a BEA combination area
        "15901": "bea-15901",  # Maui + Kalawao
        "09001": "us-county-09001",  # Fairfield County CT, legacy county
    }


def test_transform_names_and_types():
    table = bea_cainc1.transform({"line3-county": fixture(DE_2014)})
    assert table.columns == ["series", "location", "date", "personal_income", "population", "per_capita_personal_income", "location_name"]
    kent = table.filter(pl.col("location") == "us-county-10001").row(0, named=True)
    assert kent["per_capita_personal_income"] == 37285 and kent["location_name"] == "Kent"
    assert table.schema["per_capita_personal_income"] == pl.Int64


def test_validation_blocks_a_partial_table():
    # Only line 3 arrived: personal income and population are entirely null, so the contract's null
    # limits stop it even though every value present is fine.
    table = bea_cainc1.transform({"line3-county": fixture(DE_2014)})
    report = validate(contract("bea-county-income", min_rows=None), table.drop("location_name"), None)
    assert report.errors == ["personal_income: 100.0% null, limit 5.0%", "population: 100.0% null, limit 5.0%"]


def test_collect_keeps_the_key_out_of_the_archive(monkeypatch):
    monkeypatch.setattr(bea_cainc1, "PAUSE_SECONDS", 0)
    ctx = FakeContext(contract("bea-county-income"), lambda url, params: fixture(DE_2014), env={"BEA_API_KEY": PLACEHOLDER_KEY})
    raw = bea_cainc1.collect(ctx)
    assert sorted(raw) == ["line1-county", "line1-state", "line2-county", "line2-state", "line3-county", "line3-state"]
    assert [(p["LineCode"], p["GeoFips"]) for _, p in ctx.calls] == [
        ("1", "STATE"), ("1", "COUNTY"), ("2", "STATE"), ("2", "COUNTY"), ("3", "STATE"), ("3", "COUNTY"),
    ]  # fmt: skip
    assert all(p["UserID"] == PLACEHOLDER_KEY and p["Year"] == "ALL" and p["TableName"] == "CAINC1" for _, p in ctx.calls)
    for body in raw.values():
        assert PLACEHOLDER_KEY.encode() not in body  # the response's Request echo is dropped
        assert b"UTCProductionTime" not in body  # changes on every request
    assert bea_cainc1.read_results(raw["line3-county"], "3")["per_capita_personal_income"].to_list() == [45359.0, 37285.0, 48080.0, 44738.0]


@pytest.mark.parametrize(
    "name, message",
    [
        ("bea_error_invalid_userid.json", "BEA API error 1: Invalid Request - Invalid API UserId."),
        ("bea_error_throttled.json", "BEA API error 7: Request Denied - exceeded Requests per minute quota."),
    ],
)
def test_api_errors_are_raised(monkeypatch, name, message):
    monkeypatch.setattr(bea_cainc1, "PAUSE_SECONDS", 0)
    ctx = FakeContext(contract("bea-county-income"), lambda url, params: fixture(name), env={"BEA_API_KEY": PLACEHOLDER_KEY})
    with pytest.raises(RuntimeError) as err:
        bea_cainc1.collect(ctx)
    assert message in str(err.value) and PLACEHOLDER_KEY not in str(err.value)


def test_http_errors_never_show_the_key(monkeypatch):
    def reject(url, params):
        request = httpx.Request("GET", url, params=params)
        raise httpx.HTTPStatusError(f"Server error '500' for url '{request.url}'", request=request, response=httpx.Response(500, request=request))

    ctx = FakeContext(contract("bea-county-income"), reject, env={"BEA_API_KEY": PLACEHOLDER_KEY})
    with pytest.raises(RuntimeError) as err:
        bea_cainc1.collect(ctx)
    assert str(err.value) == "BEA CAINC1 line 1 STATE: HTTP 500 from https://apps.bea.gov/api/data"
    assert err.value.__suppress_context__  # the original error, which has the URL with the key, is not chained


def test_missing_key_is_reported_before_any_request():
    ctx = FakeContext(contract("bea-county-income"), lambda url, params: pytest.fail("no request expected"))
    with pytest.raises(RuntimeError, match="BEA_API_KEY"):
        bea_cainc1.collect(ctx)
