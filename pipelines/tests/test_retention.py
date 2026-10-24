"""Storage retention: which versions expire, and the two-phase blob sweep that never races a publish."""

import os
from datetime import datetime, timedelta, timezone

from datasites_core.layout import blob_key, manifest_key, version_key
from datasites_core.publish import run_dataset
from datasites_core.retention import CANDIDATES_KEY, collect_garbage, load_candidates, retained_versions
from datasites_core.store import get_json

from conftest import GL_URL, at


def age_everything(store, when: datetime) -> None:
    """Backdate every object, as if it had been written at `when`."""
    stamp = when.timestamp()
    for path in store.root.rglob("*"):
        if path.is_file():
            os.utime(path, (stamp, stamp))


def publish_daily_versions(contract, store, upstream, days):
    """One new version per day: each day nudges the latest global value so the content changes."""
    versions, value = [], 426.10
    for day in days:
        new = value + 0.01
        upstream.replace(GL_URL, f"2026,7,2026.542,{value:.2f},0.10".encode(), f"2026,7,2026.542,{new:.2f},0.10".encode())
        value = new
        result = run_dataset(contract, store, fetcher=upstream, now=at(day))
        assert result.outcome == "published"
        versions.append(result.version)
    return versions


def orphan(store, name: bytes) -> str:
    """A blob no version references, like one left behind by an expired version."""
    import hashlib

    digest = hashlib.sha256(name).hexdigest()
    store.put(blob_key(digest), name, "application/octet-stream")
    return blob_key(digest)


def test_retained_versions_keep_current_previous_snapshots_quarantined_and_recent():
    manifest = {
        "current": "2026-10-21.c",
        "previous": ["2026-10-20.p"],
        "snapshots": {"2026-08": "2026-08-01.s"},
        "quarantined": ["2026-08-05.q"],
    }
    versions = ["2026-10-21.c", "2026-10-20.p", "2026-08-01.s", "2026-08-05.q", "2026-08-02.old", "2026-10-01.recent"]
    keep = retained_versions(manifest, versions, now=datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert keep >= {"2026-10-21.c", "2026-10-20.p", "2026-08-01.s", "2026-08-05.q", "2026-10-01.recent"}
    assert "2026-08-02.old" not in keep


def test_expired_versions_go_and_their_blobs_are_swept_one_run_later(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))  # the October snapshot
    versions = publish_daily_versions(contract, store, upstream, range(10, 22))
    expired = versions[0]  # 2026-10-10: past the 10 previous versions, not a snapshot
    expired_doc, _ = get_json(store, version_key("climatedata", "co2-monthly", expired))
    current_doc, _ = get_json(store, version_key("climatedata", "co2-monthly", versions[-1]))
    current_blobs = {blob_key(e["blob"]) for e in current_doc["files"].values()}
    only_expired = {blob_key(e["blob"]) for e in expired_doc["files"].values()} - current_blobs
    assert only_expired, "the expired version should own some blobs no other version shares"
    age_everything(store, at(21))

    first = collect_garbage(store, now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert first.versions_deleted == [f"climatedata/co2-monthly@{expired}"]
    assert not store.exists(version_key("climatedata", "co2-monthly", expired))
    assert first.objects_deleted == []  # marked, not deleted, on the first pass
    assert only_expired <= set(load_candidates(store)["keys"])

    second = collect_garbage(store, now=datetime(2026, 12, 8, tzinfo=timezone.utc))
    assert set(second.objects_deleted) >= only_expired
    assert not any(store.exists(k) for k in only_expired)
    assert all(store.exists(k) for k in current_blobs)  # the current version is untouched
    manifest, _ = get_json(store, manifest_key("climatedata", "co2-monthly"))
    assert store.exists(version_key("climatedata", "co2-monthly", manifest["snapshots"]["2026-10"]))


def test_marks_must_age_a_day_before_anything_is_deleted(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    stray = orphan(store, b"left behind")
    age_everything(store, at(9))
    collect_garbage(store, now=at(10))
    assert collect_garbage(store, now=at(10, 18)).objects_deleted == []  # 4 hours later: too soon
    assert store.exists(stray)
    assert collect_garbage(store, now=at(11, 15)).objects_deleted == [stray]
    assert not store.exists(stray)


def test_a_marked_blob_written_again_is_spared(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    stray = orphan(store, b"reused later")
    age_everything(store, at(9))
    collect_garbage(store, now=at(10))
    assert stray in load_candidates(store)["keys"]
    store.put(stray, b"reused later", "application/octet-stream")  # a publish re-uploaded it after the mark
    report = collect_garbage(store, now=at(12))
    assert report.objects_deleted == [] and report.candidates_spared == 1
    assert store.exists(stray)


def test_a_publish_rewrites_marked_blobs_instead_of_reusing_them(contract, store, upstream):
    first = run_dataset(contract, store, fetcher=upstream, now=at(9))
    doc, _ = get_json(store, version_key("climatedata", "co2-monthly", first.version))
    reused = blob_key(next(iter(doc["files"].values()))["blob"])
    # Pretend the sweep marked it (as it would after a version expired), then publish content that needs it.
    store.put(CANDIDATES_KEY, b'{"marked_at": "2026-10-09T15:00:00Z", "keys": ["%s"]}' % reused.encode(), "application/json")
    age_everything(store, at(9))
    upstream.replace(GL_URL, b"426.10,0.10", b"426.11,0.10")
    assert run_dataset(contract, store, fetcher=upstream, now=at(12)).outcome == "published"
    written = datetime.fromtimestamp(os.path.getmtime(store._path(reused)), timezone.utc)
    assert written > at(9) + timedelta(hours=1)  # rewritten, so a sweep comparing write times spares it


def test_dry_run_changes_nothing(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    stray = orphan(store, b"still here")
    age_everything(store, at(9))
    report = collect_garbage(store, now=at(10), dry_run=True)
    assert report.candidates_marked >= 1
    assert not store.exists(CANDIDATES_KEY) and store.exists(stray)


def test_raw_archives_named_in_pipeline_state_are_kept(contract, store, upstream):
    run_dataset(contract, store, fetcher=upstream, now=at(9))
    raw_keys = [k for k in store.list("raw/")]
    assert raw_keys
    age_everything(store, at(9))
    collect_garbage(store, now=at(10))
    assert not set(raw_keys) & set(load_candidates(store)["keys"])
