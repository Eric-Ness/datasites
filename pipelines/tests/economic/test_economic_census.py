import httpx
import pytest

from datasites_economic import census_acs_income as acs
from economic_helpers import FakeContext, at, contract, fixture

COUNTIES = "census_acs1_2016_b01001_counties_ca.json"  # ACS API handbook, Figure 2.5
STATES = "census_acs1_2016_b01001_states.json"  # ACS API handbook, Figure 2.4
HMONG = "census_acs1_2019_b02015_states.json"  # Census Data API User Guide, ACS example
ANNOTATED = "census_acs1_2024_s0101_us_annotations.json"  # Notes on ACS Estimate and Annotation Values
FAKE_KEY = "not-a-real-census-key"


def test_county_rows_get_five_digit_fips_ids():
    df = acs.read_response(fixture(COUNTIES), "B01001_001E")
    assert df.select("location", "location_name", "estimate").to_dicts()[:2] == [
        {"location": "us-county-06001", "location_name": "Alameda County, California", "estimate": 1647704},
        {"location": "us-county-06007", "location_name": "Butte County, California", "estimate": 226864},
    ]
    assert df["location"].to_list()[-2:] == ["us-county-06037", "us-county-06053"]


def test_state_rows_get_usps_ids():
    df = acs.read_response(fixture(STATES), "B01001_001E")
    assert dict(zip(df["location"], df["estimate"])) == {"us-al": 4863300, "us-ca": 39250017, "us-ri": 1056426}


def test_nulls_and_zeros_are_kept_apart():
    df = acs.read_response(fixture(HMONG), "B02015_009E", "B02015_009M")
    by_state = {r["location"]: r for r in df.iter_rows(named=True)}
    assert by_state["us-id"]["estimate"] is None and by_state["us-id"]["moe"] is None  # no data for Idaho
    assert by_state["us-id"]["estimate_annotation"] is None
    assert (by_state["us-la"]["estimate"], by_state["us-la"]["moe"]) == (0, 224)  # a real zero
    assert (by_state["us-il"]["estimate"], by_state["us-il"]["moe"]) == (655, 511)


def test_sentinel_values_become_null_with_their_annotation():
    df = acs.read_response(fixture(ANNOTATED), "S0101_C02_001E", "S0101_C02_001M")
    assert df.to_dicts() == [
        {
            "location": "usa",
            "location_name": "United States",
            "estimate": None,
            "estimate_annotation": "(X)",
            "moe": None,
            "moe_annotation": "(X)",
        }
    ]


def test_a_response_without_median_household_income_is_refused():
    with pytest.raises(ValueError, match="expected B19013_001E and B19013_001M"):
        acs.transform({"2016-county": fixture(COUNTIES)})


def responses(latest: int, data: bytes):
    """Keyless metadata answers 404 until a release is out; data requests return `data`."""

    def respond(url, params):
        if url.endswith(".json"):
            year = int(url.split("/data/")[1].split("/")[0])
            if year > latest:
                request = httpx.Request("GET", url)
                raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
            return b"{}"
        return data

    return respond


def test_latest_release_comes_from_the_keyless_metadata():
    ctx = FakeContext(contract("census-acs-income"), responses(2024, b"[]"), now=at(24))
    assert acs.latest_release(ctx) == 2024
    assert [url for url, _ in ctx.calls] == ["https://api.census.gov/data/2025/acs/acs5.json", "https://api.census.gov/data/2024/acs/acs5.json"]
    assert all(params == {} for _, params in ctx.calls)  # no key sent to the metadata endpoint


def test_collect_requests_every_release_and_geography():
    ctx = FakeContext(contract("census-acs-income"), responses(2024, fixture(STATES)), env={"CENSUS_API_KEY": FAKE_KEY})
    raw = acs.collect(ctx)
    data_calls = [(url, params) for url, params in ctx.calls if not url.endswith(".json")]
    assert len(raw) == len(data_calls) == (2024 - 2009 + 1) * 3
    assert data_calls[0] == (
        "https://api.census.gov/data/2009/acs/acs5",
        {"get": "NAME,B19013_001E,B19013_001M", "for": "county:*", "key": FAKE_KEY},
    )
    assert [p["for"] for _, p in data_calls[-3:]] == ["county:*", "state:*", "us:1"]
    assert sorted(raw)[:3] == ["2009-county", "2009-state", "2009-us"] and "2024-us" in raw


def test_a_rejected_key_is_reported_without_the_key():
    # Without a valid key the API redirects to an HTML page; httpx follows the redirect and gets 200.
    ctx = FakeContext(contract("census-acs-income"), responses(2024, fixture("census_missing_key.html")), env={"CENSUS_API_KEY": FAKE_KEY})
    with pytest.raises(RuntimeError, match="is CENSUS_API_KEY valid") as err:
        acs.collect(ctx)
    assert FAKE_KEY not in str(err.value)


def test_missing_key_is_reported_before_any_data_request():
    ctx = FakeContext(contract("census-acs-income"), responses(2024, b"[]"))
    with pytest.raises(RuntimeError, match="CENSUS_API_KEY"):
        acs.collect(ctx)
