import polars as pl
import pytest
from climate_helpers import Upstream, global_raw, relaxed

from datasites_climate import global_temperature as gt
from datasites_core.contracts import load_contract
from datasites_core.publish import run_dataset
from datasites_core.validate import validate


def rows(table, series, location):
    part = table.filter((pl.col("series") == series) & (pl.col("location") == location))
    return {r["date"]: r for r in part.iter_rows(named=True)}


def test_shape_series_and_baselines():
    table = gt.transform(global_raw())
    contract = load_contract("climatedata/global-temperature")
    assert table.columns == contract.columns
    assert set(table["series"]) == {"gistemp", "hadcrut5"}
    assert set(table["location"]) == {"world", "nh", "sh"}
    baselines = dict(table.group_by("series").agg(pl.col("baseline").unique()).iter_rows())
    assert baselines == {"gistemp": ["1951-1980"], "hadcrut5": ["1961-1990"]}
    report = validate(contract, table, None)
    assert [e for e in report.errors if "rows, the contract requires" not in e] == []


def test_gistemp_values_written_without_leading_zero():
    world = rows(gt.transform(global_raw()), "gistemp", "world")
    assert world["1880-01"]["value"] == -0.19  # written "-.19"
    assert world["1881-03"]["value"] == 0.03  # written ".03"
    assert world["1880-01"]["lower"] is None and world["1880-01"]["upper"] is None
    assert world["2026-09"]["value"] == 1.38
    sh = rows(gt.transform(global_raw()), "gistemp", "sh")
    assert sh["1880-01"]["value"] == 0.0  # written ".00"


def test_gistemp_placeholder_months_are_dropped():
    # 2026 has '***' for October-December, which are months not yet analysed, not missing data.
    table = gt.transform(global_raw())
    gistemp = table.filter(pl.col("series") == "gistemp")
    assert gistemp["date"].max() == "2026-09"
    assert gistemp["value"].null_count() == 0
    assert len(rows(table, "gistemp", "world")) == 3 * 12 + 2 * 12 + 9  # 1880-82, 2024-25, 2026 to September


def test_hadcrut_values_and_confidence_limits():
    world = rows(gt.transform(global_raw()), "hadcrut5", "world")
    assert world["1850-01"]["value"] == -0.7339555
    assert world["1850-01"]["lower"] == -1.0368209
    assert world["1850-01"]["upper"] == -0.43109012
    assert world["2026-09"]["value"] == 1.2804134
    for r in world.values():
        assert r["lower"] <= r["value"] <= r["upper"]


def test_format_changes_fail_loudly():
    raw = global_raw()
    with pytest.raises(ValueError, match="header"):
        gt.hadcrut(raw["hadcrut5-world"].replace(b"Anomaly (deg C)", b"Anomaly"), "world")
    with pytest.raises(ValueError, match="title"):
        gt.gistemp(raw["gistemp-world"].split(b"\n", 1)[1], "world")
    with pytest.raises(ValueError, match="columns"):
        gt.gistemp(raw["gistemp-world"].replace(b",Jan,", b",January,"), "world")


def test_end_to_end_and_validation_blocks_a_bad_value(store):
    contract = relaxed("climatedata/global-temperature")
    bodies = {gt.RESOURCES[name]: body for name, body in global_raw().items()}
    first = run_dataset(contract, store, fetcher=Upstream(bodies))
    assert first.outcome == "published", first.report.as_dict()

    # A unit slip (anomaly published in hundredths of a degree) must not reach the API.
    url = gt.RESOURCES["gistemp-world"]
    bad = {**bodies, url: bodies[url].replace(b"2026,1.08,", b"2026,108,")}
    blocked = run_dataset(contract, store, fetcher=Upstream(bad))
    assert blocked.outcome == "blocked"
    assert any(e.startswith("value:") for e in blocked.report.errors), blocked.report.errors
