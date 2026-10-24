import polars as pl
import pytest
from climate_helpers import PROCDATE, FakeGet, Upstream, fixture, nclimdiv_bodies, nclimdiv_raw, relaxed

from datasites_climate import nclimdiv, nclimdiv_county, nclimdiv_state
from datasites_core import geo
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.publish import run_dataset
from datasites_core.validate import validate

COUNTIES = {"us-county-01001", "us-county-09001", "us-county-11001", "us-county-15001", "us-county-02290"}


@pytest.fixture(scope="module")
def county():
    return nclimdiv_county.transform(nclimdiv_raw("cy"))


def one(table, series, location, date):
    rows = table.filter((pl.col("series") == series) & (pl.col("location") == location) & (pl.col("date") == date))
    assert len(rows) == 1, rows
    return rows.row(0, named=True)


def fixed_width(name: str, code: str, element: str, year: str, *, county_file: bool = True) -> list[float]:
    """Independent reading of one record: twelve 7-character fields after the 11 (or 10) character prefix."""
    prefix = f"{code}{element}{year}"
    start = 11 if county_file else 10
    for line in fixture(name).decode().splitlines():
        if line.startswith(prefix):
            return [float(line[start + 7 * i : start + 7 * (i + 1)]) for i in range(12)]
    raise AssertionError(f"{prefix} not in {name}")


def test_state_code_table_matches_the_registry():
    states = {loc["fips"]: loc["usps"] for loc in geo.load().values() if loc["level"] == "us-state"}
    assert len(nclimdiv.NCEI_STATES) == 50
    for ncei, (fips, usps) in nclimdiv.NCEI_STATES.items():
        assert states[fips] == usps, (ncei, fips, usps)
    # alphabetical, not FIPS: 02 is Arizona (FIPS 04), 49 Hawaii, 50 Alaska
    assert nclimdiv.NCEI_STATES["02"] == ("04", "AZ")
    assert nclimdiv.NCEI_STATES["49"] == ("15", "HI")
    assert nclimdiv.NCEI_STATES["50"] == ("02", "AK")


def test_county_table_shape_and_locations(county):
    contract = load_contract("climatedata/county-climate")
    assert county.columns == contract.columns
    assert set(county["series"].unique()) == {"tavg", "tmax", "tmin", "pcpn"}
    assert set(county["location"].unique()) == COUNTIES
    # NCEI's DC code 18511 is FIPS 11001, not Maryland 24511
    assert "us-county-24511" not in set(county["location"])
    report = validate(contract, county, None)
    assert [e for e in report.errors if "rows, the contract requires" not in e] == []


def test_connecticut_uses_legacy_counties_named_in_the_contract():
    names = load_contract("climatedata/county-climate").locations.names()
    assert names["us-county-09001"] == "Fairfield County, CT (pre-2022)"
    assert "us-county-09110" in names  # the registry's planning regions are still known
    assert len([k for k in names if k.startswith("us-county-090")]) == 8


def test_record_starts_follow_the_files(county):
    first = county.group_by("location").agg(pl.col("date").min()).sort("location")
    assert dict(first.iter_rows()) == {
        "us-county-01001": "1895-01",
        "us-county-02290": "1925-01",  # Alaska from 1925
        "us-county-09001": "1895-01",
        "us-county-11001": "1895-01",
        "us-county-15001": "1991-01",  # Hawaii from 1991
    }


def test_unobserved_months_are_dropped_and_markers_never_published(county):
    # 2026 records end in -99.90 (temperature) and -9.99 (precipitation) for October to December
    assert county["date"].max() == "2026-09"
    assert county.filter(pl.col("date") >= "2026-10").is_empty()
    assert county["value"].null_count() == 0
    assert county.filter(pl.col("series") != "pcpn")["value"].min() > -60
    assert county.filter(pl.col("series") == "pcpn")["value"].min() >= 0
    per_place = county.group_by("series", "location").agg(pl.col("date").max())
    assert set(per_place["date"]) == {"2026-09"}


def test_values_normals_and_anomalies(county):
    row = one(county, "tavg", "us-county-01001", "1895-01")
    assert row["value"] == 44.0
    assert row["normal"] == 46.1  # climdiv-norm-tmpccy 01001020010, January
    assert row["anomaly"] == -2.1

    dc = one(county, "tavg", "us-county-11001", "2026-09")
    assert dc["value"] == 72.1 and dc["normal"] == 69.7 and dc["anomaly"] == 2.4

    wet = one(county, "pcpn", "us-county-09001", "2026-07")
    assert wet["value"] == 10.31 and wet["normal"] == 4.27 and wet["anomaly"] == 6.04

    cold = one(county, "tmin", "us-county-02290", "1925-01")
    assert cold["value"] == fixed_width("climdiv-tmincy.txt", "50290", "28", "1925")[0]


def test_ranks_match_an_independent_count(county):
    # Rank of Autauga's July 2026 average temperature among all Julys, computed straight from the fixture.
    julys = [fixed_width("climdiv-tmpccy.txt", "01001", "02", str(y))[6] for y in range(1895, 2027)]
    target = julys[-1]
    expected = 1 + sum(1 for v in julys if v > target)
    assert one(county, "tavg", "us-county-01001", "2026-07")["rank"] == expected

    ranks = county.filter((pl.col("series") == "tavg") & (pl.col("location") == "us-county-01001") & pl.col("date").str.ends_with("-07"))
    assert ranks["rank"].min() == 1 and ranks["rank"].max() <= 132
    warmest = ranks.sort("value", descending=True).row(0, named=True)
    assert warmest["rank"] == 1 and warmest["value"] == max(julys)
    # Hawaii is ranked only among its own years (1991 on)
    assert county.filter((pl.col("location") == "us-county-15001") & (pl.col("series") == "tavg"))["rank"].max() <= 36


def test_ties_share_the_smallest_rank():
    table = nclimdiv_county.transform(nclimdiv_raw("cy"))
    julys = table.filter((pl.col("series") == "pcpn") & (pl.col("location") == "us-county-01001") & pl.col("date").str.ends_with("-07"))
    counts = julys.group_by("value").agg(pl.len(), pl.col("rank").n_unique().alias("ranks"))
    tied = counts.filter(pl.col("len") > 1)
    assert len(tied), "the fixture has tied July totals"
    assert tied["ranks"].max() == 1


def test_state_table_skips_regions_and_maps_the_nation():
    table = nclimdiv_state.transform(nclimdiv_raw("st"))
    assert set(table["location"].unique()) == {"us-al", "us-hi", "usa"}  # 101 (Northeast Region) is skipped
    row = one(table, "tavg", "usa", "1895-01")
    assert row["value"] == 26.69  # national values carry hundredths
    assert row["normal"] == fixed_width("climdiv-norm-tmpcst.txt", "1100", "02", "0010", county_file=False)[0]
    assert table.filter(pl.col("location") == "us-hi")["date"].min() == "1991-01"
    contract = load_contract("climatedata/us-climate")
    report = validate(contract, table, None)
    assert [e for e in report.errors if "rows, the contract requires" not in e] == []


def test_malformed_files_raise():
    raw = nclimdiv_raw("cy")
    with pytest.raises(ValueError, match="characters wide"):
        nclimdiv.parse(raw["tavg"].replace(b"  44.00  38.20", b" 44.00  38.20", 1), level="cy", series="tavg")
    with pytest.raises(ValueError, match="element code"):
        nclimdiv.parse(raw["pcpn"], level="cy", series="tavg")
    with pytest.raises(ValueError, match="no records"):
        nclimdiv.parse(b"\n", level="cy", series="tavg")
    with pytest.raises(ValueError, match="YYYYMMDD"):
        nclimdiv.procdate(b"<html>Moved</html>")
    # an NCEI state code outside 01-50 (say a territory added later) must not be dropped silently
    unknown = {**raw, "tavg": raw["tavg"].replace(b"01001021895", b"51001021895", 1)}
    with pytest.raises(pl.exceptions.InvalidOperationError):
        nclimdiv_county.transform(unknown)


def test_resources_follow_procdate(monkeypatch):
    fake = FakeGet({nclimdiv.PROCDATE_URL: b"20261006\n"})
    monkeypatch.setattr(CollectContext, "get", fake)
    ctx = CollectContext(load_contract("climatedata/county-climate"), None)
    urls = nclimdiv_county.resources(ctx)
    assert urls["tavg"] == nclimdiv.BASE + "climdiv-tmpccy-v1.0.0-20261006"
    assert urls["pcpn-normals"] == nclimdiv.BASE + "climdiv-norm-pcpncy-v1.0.0-20261006"
    assert len(urls) == 8
    assert nclimdiv_state.resources(ctx)["tmax"].endswith("climdiv-tmaxst-v1.0.0-20261006")


def _run(contract, store, upstream, monkeypatch, procdate=PROCDATE):
    monkeypatch.setattr(CollectContext, "get", FakeGet({nclimdiv.PROCDATE_URL: procdate.encode()}))
    return run_dataset(contract, store, fetcher=upstream)


def test_end_to_end_publishes_and_then_is_unchanged(store, monkeypatch):
    contract = relaxed("climatedata/county-climate")
    upstream = Upstream(nclimdiv_bodies("cy"))
    first = _run(contract, store, upstream, monkeypatch)
    assert first.outcome == "published", first.report.as_dict()
    assert first.report.status == "pass"

    from datasites_core.layout import manifest_key, version_key
    from datasites_core.store import get_json

    manifest, _ = get_json(store, manifest_key("climatedata", "county-climate"))
    doc, _ = get_json(store, version_key("climatedata", "county-climate", manifest["current"]))
    assert doc["coverage"] == {"start": "1895-01", "end": "2026-09"}
    assert doc["locations"]["us-county-09001"] == "Fairfield County, CT (pre-2022)"
    assert doc["locations"]["us-county-11001"] == "District of Columbia"
    entry = doc["files"]["tavg/us-county-01001.csv"]
    body, _ = store.get(f"blobs/sha256/{entry['blob'][:2]}/{entry['blob']}")
    lines = body.decode().splitlines()
    assert lines[0] == "date,value,normal,anomaly,rank"
    assert lines[1].startswith("1895-01,44.0,46.1,-2.1,")

    again = _run(contract, store, upstream, monkeypatch)
    assert again.outcome == "unchanged"
    assert again.version == first.version


def test_validation_blocks_an_out_of_range_value(store, monkeypatch):
    # A record whose value slipped past the missing-value markers (e.g. a new marker such as 999.99).
    contract = relaxed("climatedata/county-climate")
    bodies = nclimdiv_bodies("cy")
    url = nclimdiv.file_urls(PROCDATE, "cy")["tavg"]
    bad = bodies[url].replace(b"01001021895  44.00", b"01001021895 999.99", 1)
    assert bad != bodies[url]
    result = _run(contract, store, Upstream({**bodies, url: bad}), monkeypatch)
    assert result.outcome == "blocked"
    assert any(e.startswith("value:") and "outside" in e for e in result.report.errors), result.report.errors


def test_validation_blocks_a_truncated_reissue(store, monkeypatch):
    contract = relaxed("climatedata/county-climate")
    bodies = nclimdiv_bodies("cy")
    assert _run(contract, store, Upstream(bodies), monkeypatch).outcome == "published"

    # Next month's files arrive with Alaska's borough missing from the temperature file.
    raw = nclimdiv_raw("cy")
    raw["tavg"] = b"\n".join(line for line in raw["tavg"].split(b"\n") if not line.startswith(b"50290"))
    later = {url: raw[name] for name, url in nclimdiv.file_urls("20261106", "cy").items()}
    result = _run(contract, store, Upstream(later), monkeypatch, procdate="20261106")
    assert result.outcome == "blocked"
    assert any("row count fell" in e for e in result.report.errors), result.report.errors
    assert any("tavg/us-county-02290" in e for e in result.report.errors)
