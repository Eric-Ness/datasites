import json

import pytest

from datasites_core.layout import catalog_key, manifest_key, state_key, version_key
from datasites_core.publish import run_dataset, set_current
from datasites_core.store import PreconditionFailed, get_json

from conftest import GL_URL, MLO_URL, at


def manifest(store):
    return get_json(store, manifest_key("climatedata", "co2-monthly"))[0]


def state(store):
    return get_json(store, state_key("climatedata", "co2-monthly"))[0]


def test_first_run_publishes_everything(contract, store, upstream):
    result = run_dataset(contract, store, fetcher=upstream, now=at(9))
    assert result.outcome == "published"
    assert result.version.startswith("2026-10-09.")
    assert result.blobs_written == 10

    m = manifest(store)
    assert m["current"] == result.version
    assert m["snapshots"] == {"2026-10": result.version}

    doc = get_json(store, version_key("climatedata", "co2-monthly", result.version))[0]
    assert doc["validation"]["status"] == "pass"
    assert doc["coverage"] == {"start": "1958-03", "end": "2026-09"}
    assert doc["source"]["source_updated_at"] == "2026-10-09T19:23:07Z"
    assert {r["name"] for r in doc["source"]["raw"]} == {"noaa-mlo", "world"}
    assert all(store.exists(r["key"]) for r in doc["source"]["raw"])  # raw archive

    catalog = get_json(store, catalog_key("climatedata"))[0]
    assert catalog["datasets"][0]["current"] == result.version
    assert catalog["datasets"][0]["coverage"]["end"] == "2026-09"

    s = state(store)
    assert s["consecutive_failures"] == 0 and s["blocked_version"] is None
    assert s["checked_at"] == "2026-10-09T14:23:00Z"


def test_unchanged_upstream_writes_only_state(contract, store, upstream):
    first = run_dataset(contract, store, fetcher=upstream, now=at(9))
    writes_before = store.writes
    second = run_dataset(contract, store, fetcher=upstream, now=at(10))
    assert second.outcome == "unchanged"
    assert second.version == first.version
    assert store.writes == writes_before + 1  # just the state file
    assert all(etag is not None for _, etag in upstream.requests[-2:])  # conditional GETs were sent
    assert state(store)["checked_at"] == "2026-10-10T14:23:00Z"


def test_regenerated_file_with_same_data_is_unchanged(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    upstream.replace(MLO_URL, b"Fri Oct  9 13:08:07 2026", b"Sat Oct 10 02:00:00 2026")
    result = run_dataset(contract, store, fetcher=upstream, now=at(10))
    assert result.outcome == "unchanged"


def test_new_month_publishes_new_version(contract, store, upstream):
    first = run_dataset(contract, store, fetcher=upstream, now=at(9))
    upstream.replace(GL_URL, b"2026,7,2026.542,426.10,0.10,427.55,0.06\n",
                     b"2026,7,2026.542,426.10,0.10,427.55,0.06\n2026,8,2026.625,425.21,0.10,427.70,0.06\n")
    second = run_dataset(contract, store, fetcher=upstream, now=at(12))
    assert second.outcome == "published"
    assert second.version != first.version
    m = manifest(store)
    assert m["current"] == second.version
    assert m["previous"] == [first.version]
    assert m["snapshots"] == {"2026-10": first.version}  # first version of the month is the snapshot
    assert m["rev"] == 2


def test_bad_data_is_quarantined_and_old_version_keeps_serving(contract, store, upstream):
    good = run_dataset(contract, store, fetcher=upstream, now=at(9))
    upstream.replace(MLO_URL, b"2026,9,2026.7083,426.07", b"2026,9,2026.7083,4260.70")
    bad = run_dataset(contract, store, fetcher=upstream, now=at(12))
    assert bad.outcome == "blocked"

    m = manifest(store)
    assert m["current"] == good.version
    assert m["quarantined"] == [bad.version]
    s = state(store)
    assert s["blocked_version"] == bad.version
    assert "outside [250.0, 600.0]" in s["last_error"]

    # Still blocked on the next run with the same bad data.
    assert run_dataset(contract, store, fetcher=upstream, now=at(13)).outcome == "blocked"
    assert manifest(store)["quarantined"] == [bad.version]  # not duplicated

    # The source fixes the value: publishes and clears the block.
    upstream.replace(MLO_URL, b"2026,9,2026.7083,4260.70", b"2026,9,2026.7083,426.07")
    fixed = run_dataset(contract, store, fetcher=upstream, now=at(14))
    assert fixed.outcome in ("published", "unchanged")
    assert state(store)["blocked_version"] is None


def test_promote_a_reviewed_quarantined_version(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    upstream.replace(MLO_URL, b"2026,9,2026.7083,426.07", b"2026,9,2026.7083,4260.70")
    bad = run_dataset(contract, store, fetcher=upstream, now=at(12))
    with pytest.raises(ValueError):
        set_current(store, contract, bad.version, allow_quarantined=False, now=at(12, 15))
    m = set_current(store, contract, bad.version, allow_quarantined=True, now=at(12, 15))
    assert m["current"] == bad.version and m["quarantined"] == []
    assert state(store)["blocked_version"] is None


def test_rollback_to_previous_version(contract, store, upstream):
    first = run_dataset(contract, store, fetcher=upstream, now=at(9))
    upstream.replace(GL_URL, b"426.10,0.10", b"426.11,0.10")
    second = run_dataset(contract, store, fetcher=upstream, now=at(12))
    assert second.outcome == "published"
    m = set_current(store, contract, first.version, allow_quarantined=False, now=at(12, 16))
    assert m["current"] == first.version
    assert m["previous"][0] == second.version
    with pytest.raises(ValueError):
        set_current(store, contract, "2020-01-01.0000000000", allow_quarantined=False)


def test_concurrent_manifest_change_loses_the_race(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    key = manifest_key("climatedata", "co2-monthly")
    body, etag = store.get(key)
    store.put(key, body.replace(b'"rev":1', b'"rev":99'), "application/json")  # someone else wrote
    with pytest.raises(PreconditionFailed):
        store.put(key, body, "application/json", if_match=etag)


def test_fetch_failure_counts_and_reraises(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))

    def broken(url, etag, last_modified):
        raise ConnectionError("gml.noaa.gov unreachable")

    for day in (10, 11, 12):
        with pytest.raises(ConnectionError):
            run_dataset(contract, store, fetcher=broken, now=at(day))
    s = state(store)
    assert s["consecutive_failures"] == 3
    assert "unreachable" in s["last_error"]
    assert s["checked_at"] == "2026-10-09T14:23:00Z"  # last successful check, so "late" can fire

    assert run_dataset(contract, store, fetcher=upstream, now=at(13)).outcome == "unchanged"
    assert state(store)["consecutive_failures"] == 0


def test_state_and_manifest_are_plain_json(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    for key in (manifest_key("climatedata", "co2-monthly"), state_key("climatedata", "co2-monthly")):
        json.loads(store.get(key)[0])
