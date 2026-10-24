"""One pipeline run: fetch -> archive raw -> change check -> transform -> validate -> upload -> flip manifest.

A pipeline module (named by the contract's `pipeline` field) provides:
    RESOURCES: dict[str, str]                    name -> URL, fetched with conditional GETs
    normalize(name, body) -> bytes                optional; strips volatile bits (timestamps) before hashing
    transform(raw: dict[str, bytes]) -> DataFrame long table with columns contract.columns
"""

from __future__ import annotations

import hashlib
import importlib
import io
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import packages_distributions, version
from types import ModuleType
from typing import Any

import polars as pl

from . import __version__ as core_version
from .contracts import Contract, site_contracts
from .fetch import Fetcher, fetch, http_date_to_iso
from .layout import blob_key, catalog_key, manifest_key, raw_key, state_key, version_key
from .outputs import build_outputs, coverage
from .store import PreconditionFailed, Store, get_json, put_json
from .validate import Report, validate

KEEP_PREVIOUS = 10


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


def _collect(module: ModuleType, contract: Contract, store: Store, state: dict[str, Any], fetcher: Fetcher, now: datetime):
    """Fetches every resource. Returns (bodies or None per resource, upstream state, normalized-content hash)."""
    normalize = getattr(module, "normalize", lambda _name, body: body)
    bodies: dict[str, bytes | None] = {}
    upstream: dict[str, Any] = {}
    for name, url in module.RESOURCES.items():
        prior = state["upstream"].get(name, {})
        got = fetcher(url, prior.get("etag"), prior.get("last_modified"))
        if got.not_modified and prior.get("raw_sha256"):
            bodies[name] = None  # loaded from the raw archive only if a rebuild is needed
            upstream[name] = prior
            continue
        if got.body is None:  # 304 with no archived copy: fetch unconditionally
            got = fetcher(url, None, None)
        assert got.body is not None
        raw_sha = sha256(got.body)
        ext = url.rsplit(".", 1)[-1] if "." in url.rsplit("/", 1)[-1] else "bin"
        key = raw_key(contract.source.id, raw_sha, ext)
        if not store.exists(key):
            store.put(key, got.body, "application/octet-stream")
        bodies[name] = got.body
        upstream[name] = {
            "url": url,
            "etag": got.etag,
            "last_modified": got.last_modified,
            "raw_sha256": raw_sha,
            "raw_key": key,
            "norm_sha256": sha256(normalize(name, got.body)),
            "retrieved_at": iso(now),
        }
    content = sha256("\n".join(f"{n}:{upstream[n]['norm_sha256']}" for n in sorted(upstream)).encode())
    return bodies, upstream, content


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
        bodies, upstream, content_hash = _collect(module, contract, store, state, fetcher, now)
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

        raw = {
            name: body if body is not None else store.get(upstream[name]["raw_key"])[0]  # type: ignore[index]
            for name, body in bodies.items()
        }
        table = module.transform(raw)
        outputs = build_outputs(contract, table)

        files: dict[str, dict[str, Any]] = {}
        blobs_written = 0
        for path, out in outputs.items():
            digest = sha256(out.body)
            if not store.exists(blob_key(digest)):
                store.put(blob_key(digest), out.body, out.content_type)
                blobs_written += 1
            files[path] = {"blob": digest, "bytes": len(out.body), "rows": out.rows, "type": out.content_type}

        tree = tree_hash({path: f["blob"] for path, f in files.items()})
        vid = f"{now.strftime('%Y-%m-%d')}.{tree[:10]}"

        previous_doc = None
        if manifest:
            previous_doc, _ = get_json(store, version_key(contract.site, contract.id, manifest["current"]))
            if previous_doc and previous_doc["tree"] == tree:
                # Rebuilt (new code or contract version) but the published bytes are identical.
                return succeed("unchanged", manifest["current"], None, blobs_written)

        previous_table = None
        if previous_doc:
            bulk = previous_doc["files"].get(f"download/{contract.id}.parquet")
            previous_table = read_table(store, bulk["blob"]) if bulk else None
        report = validate(contract, table, previous_table)

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
            return RunResult(contract.ref, "blocked", already_quarantined, report, blobs_written)

        doc = {
            "version": vid,
            "site": contract.site,
            "dataset": contract.id,
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
                "raw": [{"name": n, "key": u["raw_key"], "sha256": u["raw_sha256"]} for n, u in sorted(upstream.items())],
            },
            "coverage": coverage(table),
            "series": contract.series,
            "locations": contract.locations,
            "validation": report.as_dict(),
            "run": run_url,
            "tree": tree,
            "files": files,
        }
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
            return RunResult(contract.ref, "blocked", vid, report, blobs_written)

        write_manifest(store, contract, advance(manifest, contract, vid, now), manifest_etag)
        rebuild_catalog(store, contract.site)
        return succeed("published", vid, report, blobs_written)

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
