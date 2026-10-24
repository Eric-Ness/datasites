import json
from datetime import datetime, timezone

import httpx
import polars as pl
import pytest
from climate_helpers import FakeGet, capitals_raw, relaxed

from datasites_climate import nasa_power_capitals as npc
from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.publish import run_dataset
from datasites_core.validate import validate

NOW = datetime(2026, 10, 24, 14, 23, tzinfo=timezone.utc)
POINTS = {"fra": ("48.8581", "2.3530"), "zaf": ("-25.7050", "28.2275")}


def power_key(iso: str, end: int = 2026) -> str:
    lat, lon = POINTS[iso]
    params = {
        "parameters": "T2M",
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": 1981,
        "end": end,
        "format": "JSON",
    }
    return FakeGet.key(npc.POWER_URL, params)


def answers(raw=None) -> dict[str, bytes]:
    raw = raw or capitals_raw()
    return {npc.PLACES_URL: raw["places"], **{power_key(iso): raw[f"power-{iso}"] for iso in POINTS}}


def series(table, name, location):
    part = table.filter((pl.col("series") == name) & (pl.col("location") == location))
    return {r["date"]: r for r in part.iter_rows(named=True)}


def power_values(iso: str, param: str) -> dict[str, float]:
    return json.loads(capitals_raw()[f"power-{iso}"])["properties"]["parameter"][param]


def test_one_capital_per_country():
    caps = npc.capitals(capitals_raw()["places"])
    assert caps.to_dicts() == [
        {"location": "fra", "capital": "Paris", "latitude": 48.8581, "longitude": 2.353},
        # South Africa has four "Admin-0 capital" features: Johannesburg is not flagged ADM0CAP, Cape Town is
        # an alternate (CAPALT), and Pretoria is larger than Bloemfontein.
        {"location": "zaf", "capital": "Pretoria", "latitude": -25.705, "longitude": 28.2275},
    ]
    # Pristina (ADM0_A3 KOS) has no ISO 3166 code; Lagos is an "Admin-0 capital alt"


def test_transform_values_and_dropped_placeholders():
    table = npc.transform(capitals_raw())
    contract = load_contract("climatedata/world-capitals")
    assert table.columns == contract.columns
    assert set(table["location"]) == {"fra", "zaf"}
    paris = series(table, "tavg", "fra")
    assert paris["2026-09"]["value"] == 19.36
    assert paris["2026-09"]["capital"] == "Paris"
    assert paris["1991-02"]["value"] == 0.0  # a real 0.0 C month, not a fill value
    assert "2026-10" not in paris  # -999.0 fill for a month not yet available
    assert min(paris) == "1981-01" and len(paris) == 45 * 12 + 9  # annual ("13") values are not months
    assert set(table["series"]) == {"tavg"}  # PRECTOTCORR is not published (see the contract)
    assert series(table, "tavg", "zaf")["2026-09"]["capital"] == "Pretoria"
    assert table["value"].null_count() == 0
    report = validate(contract, table, None)
    assert [e for e in report.errors if "rows, the contract requires" not in e] == []


def test_normal_anomaly_and_rank_match_an_independent_calculation():
    table = npc.transform(capitals_raw())
    t2m = power_values("fra", "T2M")
    julys = {int(k[:4]): v for k, v in t2m.items() if k[4:] == "07" and v != -999.0}
    normal = round(sum(v for y, v in julys.items() if 1991 <= y <= 2020) / 30, 2)
    row = series(table, "tavg", "fra")["2026-07"]
    assert row["normal"] == normal
    assert row["anomaly"] == round(23.9 - normal, 2)
    assert row["rank"] == 1 + sum(1 for v in julys.values() if v > 23.9)
    pretoria = series(table, "tavg", "zaf")
    winter = [r for d, r in pretoria.items() if d.endswith("-07")]
    assert max(r["rank"] for r in winter) <= len(winter)
    assert min(r["rank"] for r in winter) == 1


def test_normalize_ignores_request_timings():
    body = capitals_raw()["power-fra"]
    doc = json.loads(body)
    doc["times"] = {"data": 9.9, "process": 1.1}
    assert npc.normalize("power-fra", json.dumps(doc).encode()) == npc.normalize("power-fra", body)
    assert npc.normalize("places", b"{}") == b"{}"


def test_collect_is_sequential_with_a_pause(monkeypatch):
    sleeps = []
    monkeypatch.setattr(npc.time, "sleep", sleeps.append)
    fake = FakeGet(answers())
    monkeypatch.setattr(CollectContext, "get", fake)
    raw = npc.collect(CollectContext(load_contract("climatedata/world-capitals"), NOW))
    assert set(raw) == {"places", "power-fra", "power-zaf"}
    assert fake.calls == [npc.PLACES_URL, power_key("fra"), power_key("zaf")]
    assert sleeps == [npc.REQUEST_DELAY_S]


def test_collect_falls_back_a_year_when_power_rejects_the_end_date(monkeypatch):
    monkeypatch.setattr(npc.time, "sleep", lambda _s: None)
    raw = capitals_raw()
    served = {npc.PLACES_URL: raw["places"], **{power_key(iso, 2026): raw[f"power-{iso}"] for iso in POINTS}}
    calls = []

    def get(ctx, url, *, params=None, headers=None, timeout=120):
        key = FakeGet.key(url, params)
        calls.append(key)
        if params and params["end"] == 2027:  # early January: the new year is "out of range"
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("422", request=request, response=httpx.Response(422, request=request))
        return served[key]

    monkeypatch.setattr(CollectContext, "get", get)
    january = datetime(2027, 1, 3, tzinfo=timezone.utc)
    out = npc.collect(CollectContext(load_contract("climatedata/world-capitals"), january))
    assert set(out) == {"places", "power-fra", "power-zaf"}
    assert calls == [npc.PLACES_URL, power_key("fra", 2027), power_key("fra", 2026), power_key("zaf", 2026)]


def test_a_response_for_the_wrong_point_is_rejected():
    raw = capitals_raw()
    raw["power-zaf"] = raw["power-fra"]
    with pytest.raises(ValueError, match="not Pretoria"):
        npc.transform(raw)
    raw = capitals_raw()
    del raw["power-fra"]
    with pytest.raises(ValueError, match="no POWER response"):
        npc.transform(raw)


def test_end_to_end_and_validation_blocks_kelvin(store, monkeypatch):
    monkeypatch.setattr(npc.time, "sleep", lambda _s: None)
    contract = relaxed("climatedata/world-capitals")
    monkeypatch.setattr(CollectContext, "get", FakeGet(answers()))
    first = run_dataset(contract, store)
    assert first.outcome == "published", first.report.as_dict()
    assert first.report.status == "pass"

    # POWER answering in Kelvin for one month (a units change upstream) must not be published.
    raw = capitals_raw()
    raw["power-fra"] = raw["power-fra"].replace(b'"202609":19.36', b'"202609":292.51')
    monkeypatch.setattr(CollectContext, "get", FakeGet(answers(raw)))
    blocked = run_dataset(contract, store)
    assert blocked.outcome == "blocked"
    assert any(e.startswith("value:") and "outside" in e for e in blocked.report.errors), blocked.report.errors
