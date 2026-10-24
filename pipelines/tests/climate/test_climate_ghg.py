import polars as pl
from climate_helpers import Upstream, ghg_raw, relaxed

from datasites_climate import noaa_gml_ghg as ghg
from datasites_core.contracts import load_contract
from datasites_core.publish import run_dataset
from datasites_core.validate import validate


def rows(table, series):
    return {r["date"]: r for r in table.filter(pl.col("series") == series).iter_rows(named=True)}


def test_shape_and_series():
    table = ghg.transform(ghg_raw())
    contract = load_contract("climatedata/greenhouse-gases")
    assert table.columns == contract.columns
    assert set(table["series"]) == {"ch4", "ch4-trend", "n2o", "n2o-trend"}
    assert set(table["location"]) == {"world"}
    # 3 + 7 months per gas in the fixtures, two series each; the blank line after the header is not a row
    assert len(table) == 2 * 2 * 10
    assert table["date"].null_count() == 0 and table["value"].null_count() == 0
    report = validate(contract, table, None)
    assert [e for e in report.errors if "rows, the contract requires" not in e] == []


def test_values_and_uncalculated_uncertainty():
    table = ghg.transform(ghg_raw())
    ch4 = rows(table, "ch4")
    assert ch4["1983-07"]["value"] == 1625.96
    assert ch4["1983-07"]["uncertainty"] == 2.25
    assert rows(table, "ch4-trend")["1983-07"]["value"] == 1635.11
    # NOAA writes -9.99 where the uncertainty is not yet calculated (its header says -9.9)
    assert ch4["2026-01"]["uncertainty"] == 1.03
    assert ch4["2026-02"]["uncertainty"] is None
    assert ch4["2026-06"]["value"] == 1934.57
    n2o = rows(table, "n2o")
    assert n2o["2001-01"]["value"] == 316.30
    assert n2o["2026-06"]["uncertainty"] is None
    assert rows(table, "n2o-trend")["2026-06"]["value"] == 339.99


def test_normalize_ignores_file_creation_timestamp():
    body = ghg_raw()["ch4"]
    regenerated = body.replace(b"Fri Oct  9 13:09:46 2026", b"Sat Oct 10 02:00:00 2026")
    assert regenerated != body
    assert ghg.normalize("ch4", regenerated) == ghg.normalize("ch4", body)


def test_end_to_end_unchanged_on_regeneration_and_blocked_on_bad_units(store):
    contract = relaxed("climatedata/greenhouse-gases")
    bodies = {ghg.RESOURCES[gas]: body for gas, body in ghg_raw().items()}
    assert run_dataset(contract, store, fetcher=Upstream(bodies)).outcome == "published"

    url = ghg.RESOURCES["ch4"]
    regenerated = {**bodies, url: bodies[url].replace(b"Fri Oct  9 13:09:46 2026", b"Sat Oct 10 02:00:00 2026")}
    assert run_dataset(contract, store, fetcher=Upstream(regenerated)).outcome == "unchanged"

    # The same file in ppm instead of ppb would be a silent 1000x error downstream.
    ppm = {**bodies, url: bodies[url].replace(b"1939.61,", b"1.93961,")}
    blocked = run_dataset(contract, store, fetcher=Upstream(ppm))
    assert blocked.outcome == "blocked"
    assert any(e.startswith("value:") and "outside" in e for e in blocked.report.errors), blocked.report.errors
