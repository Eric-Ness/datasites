"""smoke: NOAA HMS daily smoke shapefiles to GeoJSON."""

import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
import pytest
import shapefile
from wildfire_helpers import FakeGet, Upstream, fixture

from datasites_core.contracts import load_contract
from datasites_core.fetch import CollectContext
from datasites_core.publish import run_dataset
from datasites_wildfire import hms_smoke

OCT = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile/2026/10/"
SEP = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile/2026/09/"
NOW = datetime(2026, 10, 24, 14, 37, tzinfo=timezone.utc)


def shp_records(body):
    z = zipfile.ZipFile(io.BytesIO(body))
    stem = z.namelist()[0].rsplit(".", 1)[0]
    r = shapefile.Reader(shp=io.BytesIO(z.read(stem + ".shp")), shx=io.BytesIO(z.read(stem + ".shx")), dbf=io.BytesIO(z.read(stem + ".dbf")))
    return r.shapes(), r.records()


def signed_area(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / 2


def test_read_day_keeps_every_polygon_and_its_category():
    body = fixture("smoke-20261019.zip")
    shapes, records = shp_records(body)
    features = hms_smoke.read_day(body)
    assert len(features) == len(records) > 0
    for feature, record, shape in zip(features, records, shapes):
        assert feature["properties"]["density"] == record["Density"]
        assert feature["properties"]["satellite"] == record["Satellite"]
        ring = feature["geometry"]["coordinates"][0]
        assert signed_area(ring) > 0  # RFC 7946: exterior rings counter-clockwise
        assert ring[0] == ring[-1]
        assert ring == [[round(x, 4), round(y, 4)] for x, y in reversed(shape.points)]  # shapefile rings are clockwise


def test_times_are_utc_from_day_of_year():
    assert hms_smoke._time("2026292 1800") == "2026-10-19T18:00:00Z"
    assert hms_smoke._time("2024060 0000") == "2024-02-29T00:00:00Z"  # leap year
    assert hms_smoke._time("") is None


def test_an_empty_day_is_valid():
    assert hms_smoke.read_day(fixture("smoke-20261024.zip")) == []


def raw_week():
    return {
        "listing-2026-10": fixture("listing-2026-10.html"),
        "listing-2026-09": fixture("listing-2026-09.html"),
        "smoke-20261019": fixture("smoke-20261019.zip"),
        "smoke-20261024": fixture("smoke-20261024.zip"),
    }


def test_build_index_and_latest():
    files, stats = hms_smoke.build(raw_week())
    assert sorted(files) == ["days/2026-10-19.geojson", "days/2026-10-24.geojson", "index.json", "latest.geojson"]
    index = json.loads(files["index.json"])
    day = index["days"][0]
    assert day["date"] == "2026-10-19" and day["polygons"] == stats["rows"]
    assert day["light"] + day["medium"] + day["heavy"] == day["polygons"]
    assert day["file_updated_at"] == "2026-10-20T09:55:00Z"  # from the folder listing
    # The newest day has no polygons yet, so latest is the newest day with smoke.
    assert index["latest"] == "2026-10-19"
    assert json.loads(files["latest.geojson"])["date"] == "2026-10-19"
    assert json.loads(files["days/2026-10-24.geojson"])["features"] == []


def test_resources_pick_the_newest_7_days(monkeypatch):
    fake = FakeGet({OCT: fixture("listing-2026-10.html"), SEP: fixture("listing-2026-09.html")})
    monkeypatch.setattr(CollectContext, "get", fake)
    urls = hms_smoke.resources(CollectContext(load_contract("wildfiredata/smoke"), NOW))
    days = sorted(n for n in urls if n.startswith("smoke-"))
    assert days == [f"smoke-202610{d}" for d in range(18, 25)]
    assert urls["smoke-20261024"] == OCT + "hms_smoke20261024.zip"
    # Early in a month the previous month's folder supplies the rest of the week.
    early = hms_smoke.resources(CollectContext(load_contract("wildfiredata/smoke"), datetime(2026, 10, 3, 1, tzinfo=timezone.utc)))
    assert sorted(n for n in early if n.startswith("smoke-")) == [f"smoke-202609{d}" for d in (27, 28, 29, 30)] + [f"smoke-2026100{d}" for d in (1, 2, 3)]


def test_a_missing_month_folder_is_skipped(monkeypatch):
    """On the 1st of a month before NOAA writes its first file, the new folder does not exist yet."""

    def get(ctx, url, **_):
        if url == "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/Shapefile/2026/11/":
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        return fixture("listing-2026-10.html")

    monkeypatch.setattr(CollectContext, "get", get)
    urls = hms_smoke.resources(CollectContext(load_contract("wildfiredata/smoke"), datetime(2026, 11, 1, 0, 37, tzinfo=timezone.utc)))
    assert "listing-2026-11" not in urls and sorted(n for n in urls if n.startswith("smoke-"))[-1] == "smoke-20261024"


def test_end_to_end_and_broken_zip(store, monkeypatch):
    fake = FakeGet({OCT: fixture("listing-2026-10.html"), SEP: fixture("listing-2026-09.html")})
    monkeypatch.setattr(CollectContext, "get", fake)
    contract = load_contract("wildfiredata/smoke")
    urls = hms_smoke.resources(CollectContext(contract, NOW))
    bodies = {urls["listing-2026-10"]: fixture("listing-2026-10.html"), urls["listing-2026-09"]: fixture("listing-2026-09.html")}
    for name, url in urls.items():
        if name.startswith("smoke-"):
            bodies[url] = fixture("smoke-20261019.zip") if name == "smoke-20261019" else fixture("smoke-20261024.zip")
    result = run_dataset(contract, store, fetcher=Upstream(bodies), now=NOW)
    assert result.outcome == "published"
    bodies[urls["smoke-20261019"]] = b"<html>404 Not Found</html>"
    with pytest.raises(zipfile.BadZipFile):
        run_dataset(contract, store, fetcher=Upstream(bodies), now=NOW)
