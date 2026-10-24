"""Storage retention: expire old versions, then garbage-collect blobs and raw archives nothing references.

What is kept, per dataset:
- the current version, every monthly snapshot (the first version published each month) and quarantined
  versions awaiting review, permanently;
- the previous versions the manifest lists, and any version published in the last KEEP_DAYS days.
Every other version document is deleted. Pinned URLs therefore stay valid for at least KEEP_DAYS days, and
the monthly snapshots are the ones to cite.

Blobs and raw archives are shared between versions and datasets, and a publish skips uploading a blob that
already exists. So deletion is two-phase, to never remove a blob an in-flight publish has just decided to reuse:
1. A run marks every unreferenced blob and raw archive in ops/gc/candidates.json, with the time it marked them.
2. A later run (at least MARK_MIN_AGE after) deletes a candidate only if it is still unreferenced and was not
   written again since it was marked. Publishes treat candidates as missing and upload them again, which
   refreshes their write time, so a blob in use by the time of the second run always survives.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .layout import blob_key, state_key, version_key
from .store import Store, get_json, put_json

KEEP_DAYS = 30
MARK_MIN_AGE = timedelta(days=1)  # longer than any ingest run, so a publish that reused a blob has committed
CANDIDATES_KEY = "ops/gc/candidates.json"
BLOB_PREFIX = "blobs/sha256/"
RAW_PREFIX = "raw/"
_MANIFEST = re.compile(r"^sites/([^/]+)/datasets/([^/]+)/manifest\.json$")
_VERSION_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.")


@dataclass
class GcReport:
    versions_kept: int = 0
    versions_deleted: list[str] = field(default_factory=list)
    objects_deleted: list[str] = field(default_factory=list)
    candidates_marked: int = 0
    candidates_spared: int = 0  # marked last time but referenced or rewritten since

    def summary(self) -> str:
        return (
            f"GC OK ({self.versions_kept} versions kept, {len(self.versions_deleted)} expired; "
            f"{len(self.objects_deleted)} unreferenced objects deleted, {self.candidates_marked} newly marked, "
            f"{self.candidates_spared} spared)"
        )


def load_candidates(store: Store) -> dict[str, Any]:
    """{"marked_at": iso or None, "keys": [...]}; publishes read it to re-upload marked blobs."""
    found, _ = get_json(store, CANDIDATES_KEY)
    return found or {"marked_at": None, "keys": []}


def retained_versions(manifest: dict[str, Any], versions: list[str], now: datetime) -> set[str]:
    keep = {manifest["current"], *manifest.get("previous", []), *manifest.get("snapshots", {}).values(), *manifest.get("quarantined", [])}
    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    for vid in versions:
        m = _VERSION_DATE.match(vid)
        if m is None or m.group(1) >= cutoff:  # unparseable ids are kept rather than guessed at
            keep.add(vid)
    return keep


def _references(store: Store, doc: dict[str, Any]) -> set[str]:
    """Every blob and raw-archive key a version document needs."""
    refs = {blob_key(e["blob"]) for e in doc.get("files", {}).values()}
    for shard in (doc.get("file_shards") or {}).values():
        refs.add(blob_key(shard))
        found = store.get(blob_key(shard))
        if found:
            refs.update(blob_key(e["blob"]) for e in json.loads(found[0]).values())
    if doc.get("locations_blob"):
        refs.add(blob_key(doc["locations_blob"]))
    refs.update(r["key"] for r in doc.get("source", {}).get("raw", []) if r.get("key"))
    return refs


def collect_garbage(store: Store, now: datetime | None = None, *, dry_run: bool = False) -> GcReport:
    now = now or datetime.now(timezone.utc)
    report = GcReport()
    live: set[str] = set()

    # 1. versions: keep the retained ones, collect what they reference, expire the rest
    for key in store.list("sites/"):
        m = _MANIFEST.match(key)
        if not m:
            continue
        site, dataset = m.groups()
        manifest, _ = get_json(store, key)
        if not manifest:
            continue
        prefix = f"sites/{site}/datasets/{dataset}/versions/"
        versions = [k[len(prefix) : -len(".json")] for k in store.list(prefix) if k.endswith(".json")]
        keep = retained_versions(manifest, versions, now)
        for vid in versions:
            if vid in keep:
                doc, _ = get_json(store, version_key(site, dataset, vid))
                if doc:
                    live |= _references(store, doc)
                report.versions_kept += 1
            else:
                report.versions_deleted.append(f"{site}/{dataset}@{vid}")
                if not dry_run:
                    store.delete(version_key(site, dataset, vid))
        # pipeline state points at raw archives it rebuilds from when the source answers 304
        state, _ = get_json(store, state_key(site, dataset))
        for info in ((state or {}).get("upstream") or {}).values():
            if isinstance(info, dict) and info.get("raw_key"):
                live.add(info["raw_key"])

    # 2. sweep last run's candidates that are still unreferenced and untouched since they were marked. Too soon
    # after the last mark, leave the marks alone: a new mark must age a full MARK_MIN_AGE before anything goes.
    previous = load_candidates(store)
    marked_at = datetime.fromisoformat(previous["marked_at"].replace("Z", "+00:00")) if previous["marked_at"] else None
    if marked_at is not None and now - marked_at < MARK_MIN_AGE:
        report.candidates_marked = len(previous["keys"])
        return report
    dated = dict(store.list_dated(BLOB_PREFIX)) | dict(store.list_dated(RAW_PREFIX))
    if marked_at is not None:
        for key in previous["keys"]:
            written = dated.get(key)
            if written is None:
                continue
            if key in live or written >= marked_at:
                report.candidates_spared += 1
                continue
            report.objects_deleted.append(key)
            del dated[key]
            if not dry_run:
                store.delete(key)

    # 3. mark everything unreferenced now, for the next run to sweep
    marks = sorted(k for k in dated if k not in live)
    report.candidates_marked = len(marks)
    if not dry_run:
        stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if marks else None
        put_json(store, CANDIDATES_KEY, {"marked_at": stamp, "keys": marks})
    return report
