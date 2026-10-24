"""One pipeline run: fetch -> archive raw -> change check -> transform -> validate -> upload -> flip manifest.

A pipeline module (named by the contract's `pipeline` field) provides how to get the raw data, one of:
    RESOURCES: dict[str, str]            name -> URL, fetched with conditional GETs (preferred), or
    resources(ctx) -> dict[str, str]     the same, computed at run time (e.g. one URL per year), or
    collect(ctx) -> dict[str, bytes]     for paged APIs; ctx.get(url, params=...) retries and sets the UA
optionally:
    normalize(name, body) -> bytes       strips volatile bits (timestamps) before change detection
and, by contract kind:
    series  transform(raw) -> DataFrame  columns: series, location, <schema>; optional location_name
    table   transform(raw) -> DataFrame  columns: <schema>
    files   build(raw) -> (dict[path, bytes], stats)   stats["rows"] is checked against checks.min_rows
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import io
import os
import subprocess
import threading
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import packages_distributions, version
from types import ModuleType
from typing import Any

import polars as pl

from . import __version__ as core_version
from .contracts import Contract, site_contracts
from .fetch import CollectContext, Fetcher, fetch, http_date_to_iso
from .layout import blob_key, catalog_key, manifest_key, raw_key, state_key, version_key
from .outputs import Output, build_table_outputs, content_type_for, coverage, iter_series_outputs
from .retention import load_candidates
from .store import PreconditionFailed, Store, dumps, get_json, put_json
from .validate import Report, validate_files, validate_series, validate_table

KEEP_PREVIOUS = 10
SHARD_THRESHOLD = 2_000  # file maps larger than this are split into one blob per series
LOCATIONS_INLINE_MAX = 500  # larger location maps go to their own blob
UPLOAD_WORKERS = 16
UPLOAD_BATCH = 64  # files in flight at once; bounds memory while uploading big versions


@dataclass
class RunResult:
    ref: str
    outcome: str  # "published" | "unchanged" | "blocked"
    version: str | None
    report: Report | None
    blobs_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.ref,
            "outcome": self.outcome,
            "version": self.version,
            "blobs_written": self.blobs_written,
            "validation": self.report.as_dict() if self.report else None,
        }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tree_hash(files: dict[str, str]) -> str:
    """Hash of the path -> blob map. Identical outputs give an identical tree hash."""
    return sha256("\n".join(f"{path}:{blob}" for path, blob in sorted(files.items())).encode())


def git_sha() -> str:
    if sha := os.environ.get("GITHUB_SHA"):
        return sha[:7]
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def pipeline_version(module: ModuleType) -> str:
    """'datasites-climate 0.1.0'. Bump the package version when a transform changes its output."""
    top = module.__name__.split(".")[0]
    dist = (packages_distributions().get(top) or [top])[0]
    try:
        return f"{dist} {version(dist)}"
    except Exception:
        return f"{dist} 0.0.0"


def empty_state() -> dict[str, Any]:
    return {
        "fingerprint": None,
        "upstream": {},
        "checked_at": None,
        "last_attempt_at": None,
        "last_success": None,
        "last_published": None,
        "source_updated_at": None,
        "consecutive_failures": 0,
        "last_error": None,
        "blocked_version": None,
    }


def load_state(store: Store, contract: Contract) -> dict[str, Any]:
    state, _ = get_json(store, state_key(contract.site, contract.id))
    return {**empty_state(), **(state or {})}


def save_state(store: Store, contract: Contract, state: dict[str, Any]) -> None:
    put_json(store, state_key(contract.site, contract.id), state)


def read_table(store: Store, blob: str) -> pl.DataFrame | None:
    found = store.get(blob_key(blob))
    return pl.read_parquet(io.BytesIO(found[0])) if found else None


def advance(manifest: dict[str, Any] | None, contract: Contract, vid: str, now: datetime) -> dict[str, Any]:
    """New manifest pointing at vid. The first version published each month is kept as a snapshot."""
    month = now.strftime("%Y-%m")
    if manifest is None:
        return {
            "site": contract.site,
            "dataset": contract.id,
            "current": vid,
            "published_at": iso(now),
            "previous": [],
            "snapshots": {month: vid},
            "quarantined": [],
            "rev": 1,
        }
    previous = [manifest["current"], *[v for v in manifest["previous"] if v not in (vid, manifest["current"])]]
    snapshots = dict(manifest["snapshots"])
    snapshots.setdefault(month, vid)
    return {
        **manifest,
        "current": vid,
        "published_at": iso(now),
        "previous": previous[:KEEP_PREVIOUS],
        "snapshots": snapshots,
        "quarantined": [v for v in manifest["quarantined"] if v != vid],
        "rev": manifest["rev"] + 1,
    }


def write_manifest(store: Store, contract: Contract, manifest: dict[str, Any], etag: str | None) -> None:
    key = manifest_key(contract.site, contract.id)
    if etag is None:
        put_json(store, key, manifest, if_none_match=True)
    else:
        put_json(store, key, manifest, if_match=etag)


def rebuild_catalog(store: Store, site: str) -> None:
    entries = []
    for contract in site_contracts(site):
        manifest, _ = get_json(store, manifest_key(site, contract.id))
        doc = None
        if manifest:
            doc, _ = get_json(store, version_key(site, contract.id, manifest["current"]))
        entries.append(
            {
                "id": contract.id,
                "kind": contract.kind,
                "title": contract.title,
                "description": contract.description,
                "current": manifest["current"] if manifest else None,
                "published_at": manifest["published_at"] if manifest else None,
                "cadence": contract.cadence,
                "check_every": contract.check_every,
                "sla": contract.sla,
                "discontinued": contract.discontinued,
                "source": {
                    "name": contract.source.name,
                    "url": contract.source.url,
                    "license": contract.source.license,
                    "commercial_ok": contract.source.commercial_ok,
                },
                "coverage": doc["coverage"] if doc else {"start": None, "end": None},
            }
        )
    put_json(store, catalog_key(site), {"site": site, "datasets": entries})


# --- fetching -----------------------------------------------------------------------------------


def _archive(store: Store, contract: Contract, name: str, url: str, body: bytes, marked: frozenset[str] = frozenset()) -> dict[str, Any]:
    raw_sha = sha256(body)
    tail = url.split("?", 1)[0].rsplit("/", 1)[-1]
    ext = tail.rsplit(".", 1)[-1] if "." in tail and len(tail.rsplit(".", 1)[-1]) <= 8 else "bin"
    key = raw_key(contract.source.id, raw_sha, ext)
    archived = len(body) <= contract.raw_archive_max_mb * 1024 * 1024
    # A key marked for garbage collection is written again, so the sweep sees it is in use (see retention.py).
    if archived and (key in marked or not store.exists(key)):
        store.put(key, body, "application/octet-stream")
    return {"raw_sha256": raw_sha, "raw_key": key if archived else None, "archived": archived, "bytes": len(body)}


def _collect(
    module: ModuleType,
    contract: Contract,
    store: Store,
    state: dict[str, Any],
    fetcher: Fetcher,
    now: datetime,
    marked: frozenset[str] = frozenset(),
):
    """Fetches every resource. Returns (bodies or None per resource, upstream state, normalized-content hash)."""
    normalize = getattr(module, "normalize", lambda _name, body: body)
    bodies: dict[str, bytes | None] = {}
    upstream: dict[str, Any] = {}

    if hasattr(module, "collect"):
        ctx = CollectContext(contract, now, store)
        for name, body in module.collect(ctx).items():
            bodies[name] = body
            upstream[name] = {
                "url": None,
                **_archive(store, contract, name, f"{name}.bin", body, marked),
                "norm_sha256": sha256(normalize(name, body)),
                "retrieved_at": iso(now),
            }
    else:
        resources = module.resources(CollectContext(contract, now, store)) if hasattr(module, "resources") else module.RESOURCES
        for name, url in resources.items():
            prior = state["upstream"].get(name, {})
            got = fetcher(url, prior.get("etag"), prior.get("last_modified"))
            if got.not_modified and prior.get("norm_sha256") and prior.get("url") == url:
                bodies[name] = None  # loaded (or refetched) only if a rebuild is needed
                upstream[name] = prior
                continue
            if got.body is None:
                got = fetcher(url, None, None)
            assert got.body is not None
            bodies[name] = got.body
            upstream[name] = {
                "url": url,
                "etag": got.etag,
                "last_modified": got.last_modified,
                **_archive(store, contract, name, url, got.body, marked),
                "norm_sha256": sha256(normalize(name, got.body)),
                "retrieved_at": iso(now),
            }
    content = sha256("\n".join(f"{n}:{upstream[n]['norm_sha256']}" for n in sorted(upstream)).encode())
    return bodies, upstream, content


def _materialize(bodies: dict[str, bytes | None], upstream: dict[str, Any], store: Store, fetcher: Fetcher) -> dict[str, bytes]:
    """Bodies skipped thanks to a 304 come from the raw archive, or are refetched if they were too big to archive."""
    raw: dict[str, bytes] = {}
    for name, body in bodies.items():
        if body is not None:
            raw[name] = body
            continue
        info = upstream[name]
        found = store.get(info["raw_key"]) if info.get("raw_key") else None
        if found is None:
            got = fetcher(info["url"], None, None)
            assert got.body is not None
            found = (got.body, "")
        raw[name] = found[0]
    return raw


# --- publishing ---------------------------------------------------------------------------------


class _Uploader:
    """Uploads content-addressed blobs, skipping ones that exist, except ones marked for garbage collection,
    which are written again so the sweep sees they are in use (see retention.py)."""

    def __init__(self, store: Store, marked: frozenset[str] = frozenset()):
        self.store = store
        self.marked = marked
        self.lock = threading.Lock()
        self.written = 0
        self._existing: set[str] | None = None

    def _known(self, seen: int) -> set[str] | None:
        """Past 50 files, one listing of the blob namespace beats thousands of existence checks."""
        if self._existing is None and seen > 50:
            self._existing = {k.rsplit("/", 1)[-1] for k in self.store.list("blobs/sha256/") if k not in self.marked}
        return self._existing

    def _put(self, digest: str, out: Output, check_exists: bool) -> None:
        key = blob_key(digest)
        if check_exists and key not in self.marked and self.store.exists(key):
            return
        self.store.put(key, out.body, out.content_type)
        with self.lock:
            self.written += 1

    def upload(self, outputs: Iterable[tuple[str, Output]]) -> dict[str, dict[str, Any]]:
        """Uploads a stream of files in bounded batches, keeping only their entries afterwards, so memory
        holds a few dozen files at a time rather than a whole county-scale version."""
        files: dict[str, dict[str, Any]] = {}
        queued: set[str] = set()
        batch: list[Future] = []
        with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
            for seen, (path, out) in enumerate(outputs, start=1):
                digest = sha256(out.body)
                files[path] = {"blob": digest, "bytes": len(out.body), "rows": out.rows, "type": out.content_type}
                known = self._known(seen)
                if digest in queued or (known is not None and digest in known):
                    continue
                queued.add(digest)
                batch.append(pool.submit(self._put, digest, out, known is None))
                if len(batch) >= UPLOAD_BATCH:
                    for future in batch:
                        future.result()
                    batch.clear()
            for future in batch:
                future.result()
        return files

    def put_json_blob(self, value: Any) -> str:
        body = dumps(value)
        digest = sha256(body)
        key = blob_key(digest)
        if key in self.marked or not self.store.exists(key):
            self.store.put(key, body, "application/json")
            self.written += 1
        return digest


def _file_map(uploader: _Uploader, files: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Small datasets keep every file in the version doc; big ones keep download/* and shard the rest by series."""
    if len(files) <= SHARD_THRESHOLD:
        return files, None
    top = {p: e for p, e in files.items() if p.startswith("download/")}
    shards: dict[str, dict[str, Any]] = {}
    for path, entry in files.items():
        if not path.startswith("download/"):
            shards.setdefault(path.split("/", 1)[0], {})[path] = entry
    return top, {name: uploader.put_json_blob(shard) for name, shard in sorted(shards.items())}


def run_dataset(
    contract: Contract,
    store: Store,
    *,
    fetcher: Fetcher = fetch,
    now: datetime | None = None,
    run_url: str | None = None,
) -> RunResult:
    now = now or datetime.now(timezone.utc)
    state = load_state(store, contract)
    state["last_attempt_at"] = iso(now)
    module = importlib.import_module(contract.pipeline)
    transform_version = pipeline_version(module)

    try:
        marked = frozenset(load_candidates(store)["keys"])
        bodies, upstream, content_hash = _collect(module, contract, store, state, fetcher, now, marked)
        fingerprint = sha256(f"{content_hash}:{contract.version}:{core_version}:{transform_version}".encode())
        manifest, manifest_etag = get_json(store, manifest_key(contract.site, contract.id))
        source_updated = max((u for u in (http_date_to_iso(x.get("last_modified")) for x in upstream.values()) if u), default=None)

        def succeed(outcome: str, vid: str | None, report: Report | None, blobs: int = 0) -> RunResult:
            state.update(
                fingerprint=fingerprint,
                upstream=upstream,
                checked_at=iso(now),
                last_success=iso(now),
                source_updated_at=source_updated or state["source_updated_at"],
                consecutive_failures=0,
                last_error=None,
                blocked_version=None,
            )
            if outcome == "published":
                state["last_published"] = iso(now)
            save_state(store, contract, state)
            return RunResult(contract.ref, outcome, vid, report, blobs)

        # Same upstream content, same contract and code: nothing can have changed.
        if manifest and state["fingerprint"] == fingerprint and not state["blocked_version"]:
            return succeed("unchanged", manifest["current"], None)

        raw = _materialize(bodies, upstream, store, fetcher)
        uploader = _Uploader(store, marked)
        table: pl.DataFrame | None = None
        known: dict[str, str] = {}
        locations: dict[str, str] = {}
        stats: dict[str, Any] = {}
        outputs: Iterable[tuple[str, Output]]
        if contract.kind == "files":
            takes_ctx = len(inspect.signature(module.build).parameters) >= 2
            built, stats = module.build(raw, CollectContext(contract, now, store)) if takes_ctx else module.build(raw)
            outputs = [(path, Output(body, content_type_for(path), 0)) for path, body in built.items()]
        else:
            frame: pl.DataFrame = module.transform(raw)
            if contract.kind == "series":
                known = contract.locations.names()
                dynamic: dict[str, str] = {}
                if "location_name" in frame.columns:
                    named = frame.group_by("location").agg(pl.col("location_name").first())
                    dynamic = dict(zip(named["location"].to_list(), named["location_name"].to_list()))
                    frame = frame.drop("location_name")
                present = sorted(frame["location"].unique().to_list())
                locations = {loc: known.get(loc) or dynamic.get(loc) or loc for loc in present}
                outputs = iter_series_outputs(contract, frame, locations)  # streamed: never all in memory
            else:
                outputs = build_table_outputs(contract, frame).items()
            table = frame

        files = uploader.upload(outputs)
        tree = tree_hash({path: f["blob"] for path, f in files.items()})
        vid = f"{now.strftime('%Y-%m-%d')}.{tree[:10]}"

        previous_doc = None
        if manifest:
            previous_doc, _ = get_json(store, version_key(contract.site, contract.id, manifest["current"]))
            if previous_doc and previous_doc["tree"] == tree:
                # Rebuilt (new code or contract version) but the published bytes are identical.
                return succeed("unchanged", manifest["current"], None, uploader.written)

        if contract.kind == "files" or table is None:
            report = validate_files(contract, set(files), stats)
        else:
            previous_table = None
            if previous_doc:
                bulk = previous_doc["files"].get(f"download/{contract.id}.parquet")
                previous_table = read_table(store, bulk["blob"]) if bulk else None
            if contract.kind == "series":
                # Only the contract's own names count as known; anything else must match its pattern.
                report = validate_series(contract, table, previous_table, known)
            else:
                report = validate_table(contract, table, previous_table)

        # The same bad content seen again on a later day: keep the existing quarantined version.
        already_quarantined = next((v for v in (manifest or {}).get("quarantined", []) if v.endswith(f".{tree[:10]}")), None)
        if report.blocking and already_quarantined:
            state.update(
                checked_at=iso(now),
                upstream=upstream,
                blocked_version=already_quarantined,
                last_error="validation blocked: " + "; ".join(report.errors),
            )
            save_state(store, contract, state)
            return RunResult(contract.ref, "blocked", already_quarantined, report, uploader.written)

        file_map, file_shards = _file_map(uploader, files)
        doc: dict[str, Any] = {
            "version": vid,
            "site": contract.site,
            "dataset": contract.id,
            "kind": contract.kind,
            "title": contract.title,
            "description": contract.description,
            "cadence": contract.cadence,
            "contract_version": contract.version,
            "transform_version": f"{transform_version}; datasites-core {core_version} ({git_sha()})",
            "source": {
                "id": contract.source.id,
                "name": contract.source.name,
                "publisher": contract.source.publisher,
                "url": contract.source.url,
                "license": contract.source.license,
                "commercial_ok": contract.source.commercial_ok,
                "attribution": contract.source.attribution,
                "retrieved_at": iso(now),
                "source_updated_at": source_updated,
                "raw": [
                    {"name": n, "key": u.get("raw_key"), "sha256": u["raw_sha256"], "bytes": u.get("bytes")}
                    for n, u in sorted(upstream.items())
                ],
            },
            "coverage": coverage(contract, table),
            "series": contract.series,
            "validation": report.as_dict(),
            "run": run_url,
            "tree": tree,
            "files": file_map,
        }
        if file_shards:
            doc["file_shards"] = file_shards
        if contract.kind == "series":
            if len(locations) > LOCATIONS_INLINE_MAX:
                doc["locations_blob"] = uploader.put_json_blob(locations)
            else:
                doc["locations"] = locations
        if contract.kind == "table":
            doc["key"] = list(contract.key)
            doc["partition"] = contract.partition
        put_json(store, version_key(contract.site, contract.id, vid), doc)  # the commit marker

        if report.blocking:
            if manifest and vid not in manifest["quarantined"]:
                write_manifest(store, contract, {**manifest, "quarantined": [*manifest["quarantined"], vid]}, manifest_etag)
            state.update(
                checked_at=iso(now),
                upstream=upstream,
                blocked_version=vid,
                last_error="validation blocked: " + "; ".join(report.errors),
            )
            save_state(store, contract, state)
            return RunResult(contract.ref, "blocked", vid, report, uploader.written)

        write_manifest(store, contract, advance(manifest, contract, vid, now), manifest_etag)
        rebuild_catalog(store, contract.site)
        return succeed("published", vid, report, uploader.written)

    except Exception as err:
        state["consecutive_failures"] += 1
        state["last_error"] = f"{type(err).__name__}: {err}"[:500]
        save_state(store, contract, state)
        raise


def set_current(store: Store, contract: Contract, vid: str, *, allow_quarantined: bool, now: datetime | None = None) -> dict:
    """Rollback (to a previous version or snapshot) or promote (a reviewed quarantined version)."""
    now = now or datetime.now(timezone.utc)
    manifest, etag = get_json(store, manifest_key(contract.site, contract.id))
    if manifest is None:
        raise ValueError(f"{contract.ref} has never been published")
    known = {*manifest["previous"], *manifest["snapshots"].values()}
    if allow_quarantined:
        known |= set(manifest["quarantined"])
    if vid not in known:
        raise ValueError(f"{vid} is not a previous, snapshot{' or quarantined' if allow_quarantined else ''} version")
    if not store.exists(version_key(contract.site, contract.id, vid)):
        raise ValueError(f"version document for {vid} is missing")
    new = advance(manifest, contract, vid, now)
    try:
        write_manifest(store, contract, new, etag)
    except PreconditionFailed:
        raise RuntimeError("the manifest changed while switching versions; run the command again") from None
    if allow_quarantined:
        state = load_state(store, contract)
        state.update(blocked_version=None, last_error=None)
        save_state(store, contract, state)
    rebuild_catalog(store, contract.site)
    return new
