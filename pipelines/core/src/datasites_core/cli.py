"""datasites run climatedata/co2-monthly --target local

Exit codes: 0 published or unchanged, 2 blocked by validation, 1 error.

With --alert-after N, a dataset whose source can't be reached (connection errors, timeouts, or 429/5xx after
every retry) counts as an error only once it has failed N runs in a row; until then the run reports it as
"upstream-down" and keeps serving the last version. Any other error fails the run at once.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from datetime import datetime, timedelta, timezone

import httpx

from . import durations
from .contracts import ContractError, all_contracts, load_contract, site_contracts
from .publish import RunResult, load_state, run_dataset, set_current
from .fetch import RETRY_STATUSES
from .store import TARGETS, open_store


def _refs(target: str) -> list[str]:
    """'climatedata/co2-monthly' -> itself; 'climatedata' -> every dataset of that site."""
    if "/" in target:
        return [target]
    contracts = site_contracts(target)
    if not contracts:
        raise ContractError(f"no datasets found for site {target!r}")
    return [c.ref for c in contracts]


def _run_url() -> str | None:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def _summary(results: list[RunResult], failures: list[tuple[str, str]], path: str, down: list[tuple[str, str]] = ()) -> None:
    lines = ["| Dataset | Outcome | Version | New blobs | Notes |", "|---|---|---|---|---|"]
    for r in results:
        notes = ""
        if r.report:
            notes = "; ".join(r.report.errors or r.report.warnings)
        lines.append(f"| {r.ref} | {r.outcome} | {r.version or ''} | {r.blobs_written} | {notes} |")
    for ref, error in failures:
        lines.append(f"| {ref} | error | | | {error} |")
    for ref, error in down:
        lines.append(f"| {ref} | upstream down (not alerting yet) | | | {error} |")
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


DUE_FRACTION = 0.1  # slack as a share of check_every
DUE_MIN_SLACK = timedelta(hours=1)  # GitHub starts scheduled runs 5-60 minutes late, unevenly


def due_slack(check_every: timedelta) -> timedelta:
    """How early a dataset counts as due: 10% of check_every, at least an hour, at most half of it.

    Scheduled runs start late by varying amounts, so two runs of a 3-hourly workflow can land 2h42m apart.
    With only 10% slack (18 minutes) that run skipped the dataset, which then refreshed every 6 hours.
    """
    return min(max(check_every * DUE_FRACTION, DUE_MIN_SLACK), check_every / 2)


def upstream_down(err: BaseException) -> bool:
    """True when err, or an error it was raised from, is the source being unreachable rather than a bug or bad data."""
    seen: set[int] = set()
    e: BaseException | None = err
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, httpx.TransportError):
            return True
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in RETRY_STATUSES:
            return True
        e = e.__cause__ or e.__context__
    return False


def not_due(contract, store, now: datetime) -> bool:
    """True when the last successful check is more recent than the contract's check_every allows."""
    checked = load_state(store, contract).get("checked_at")
    if not checked:
        return False
    elapsed = now - datetime.fromisoformat(checked.replace("Z", "+00:00"))
    every = durations.parse(contract.check_every)
    return elapsed < every - due_slack(every)


def cmd_run(args: argparse.Namespace) -> int:
    store = open_store(args.target)
    results: list[RunResult] = []
    failures: list[tuple[str, str]] = []
    skipped = 0
    down: list[tuple[str, str]] = []
    needs_credentials: list[str] = []
    now = datetime.now(timezone.utc)
    for ref in _refs(args.dataset):
        try:
            contract = load_contract(ref)
            missing = [name for name in contract.requires_env if not os.environ.get(name)]
            if missing:
                needs_credentials.append(ref)
                print(json.dumps({"dataset": ref, "outcome": "needs-credentials", "missing": missing}))
                continue
            if not args.force and not_due(contract, store, now):
                skipped += 1
                print(json.dumps({"dataset": ref, "outcome": "not-due", "check_every": contract.check_every}))
                continue
            result = run_dataset(contract, store, run_url=_run_url())
            results.append(result)
            print(json.dumps(result.as_dict()))
        except Exception as err:  # keep going so one broken source doesn't stop the others
            message = f"{type(err).__name__}: {err}"
            misses = load_state(store, contract).get("consecutive_failures", 0) if upstream_down(err) else None
            if misses is not None and misses < args.alert_after:
                down.append((ref, message))
                print(json.dumps({"dataset": ref, "outcome": "upstream-down", "error": str(err), "misses": misses, "alert_after": args.alert_after}))
                continue
            failures.append((ref, message))
            print(json.dumps({"dataset": ref, "outcome": "error", "error": str(err)}), file=sys.stderr)
    if args.summary:
        _summary(results, failures, args.summary, down)
    if failures:
        return 1
    if any(r.outcome == "blocked" for r in results):
        return 2
    counts = {o: sum(1 for r in results if r.outcome == o) for o in ("published", "unchanged")}
    print(
        f"RUN OK {len(results) + skipped + len(needs_credentials) + len(down)} datasets "
        f"(published {counts['published']}, unchanged {counts['unchanged']}, not due {skipped}, "
        f"needs credentials {len(needs_credentials)}{f', upstream down {len(down)}' if down else ''})"
    )
    return 0


def cmd_set(args: argparse.Namespace, *, promote: bool) -> int:
    contract = load_contract(args.dataset)
    manifest = set_current(open_store(args.target), contract, args.version, allow_quarantined=promote)
    print(json.dumps({"dataset": contract.ref, "current": manifest["current"], "rev": manifest["rev"]}))
    return 0


def cmd_geo(args: argparse.Namespace) -> int:
    from . import geo

    doc = geo.build()
    counts = {level: sum(1 for loc in doc["locations"] if loc["level"] == level) for level in geo.LEVELS}
    summary = ", ".join(f"{n} {level}" for level, n in counts.items())
    if args.action == "check":
        if not geo.check(doc):
            print(f"packages/geo/src/locations.json is out of date ({summary}). Run: uv run datasites geo build", file=sys.stderr)
            return 1
        print(f"geo registry up to date ({summary})")
        return 0
    geo.write(doc, geo.cldr_license())
    print(f"wrote packages/geo/src/locations.json ({summary})")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    from .retention import collect_garbage

    report = collect_garbage(open_store(args.target), dry_run=args.dry_run)
    for ref in report.versions_deleted:
        print(f"{'would expire' if args.dry_run else 'expired'}  {ref}")
    for key in report.objects_deleted:
        print(f"{'would delete' if args.dry_run else 'deleted'}  {key}")
    print(("DRY RUN " if args.dry_run else "") + report.summary())
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    contracts = site_contracts(args.site) if args.site else all_contracts()
    if args.site and not contracts:
        raise ContractError(f"no dataset contracts for site {args.site!r}")
    for c in contracts:
        importlib.import_module(c.pipeline)  # the pipeline module named by the contract must exist
        print(f"ok  {c.ref}  [{c.kind}] ({c.source.license}, commercial_ok={c.source.commercial_ok})")
    print(f"{len(contracts)} contracts valid")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datasites", description="Run and manage datasites pipelines.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch, validate and publish a dataset (or every dataset of a site)")
    run.add_argument("dataset", help="<site>/<dataset> or <site>")
    run.add_argument("--target", choices=TARGETS, default="local")
    run.add_argument("--summary", help="append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    run.add_argument("--force", action="store_true", help="run even if the dataset was checked within its check_every")
    run.add_argument(
        "--alert-after", type=int, default=1, metavar="N",
        help="fail the run for an unreachable source only once the dataset has failed N runs in a row (default 1)",
    )

    for name, text in (("rollback", "point a dataset back at a previous version"), ("promote", "publish a reviewed quarantined version")):
        p = sub.add_parser(name, help=text)
        p.add_argument("dataset", help="<site>/<dataset>")
        p.add_argument("version")
        p.add_argument("--target", choices=TARGETS, default="local")

    lint = sub.add_parser("lint", help="check dataset contracts and that their pipeline modules import")
    lint.add_argument("--site", help="only this site's contracts")

    gc = sub.add_parser("gc", help="expire old versions and delete storage nothing references (see retention.py)")
    gc.add_argument("--target", choices=TARGETS, default="local")
    gc.add_argument("--dry-run", action="store_true", help="report what would be removed without changing anything")

    geo_parser = sub.add_parser("geo", help="build or check the shared location registry (packages/geo)")
    geo_parser.add_argument("action", choices=("build", "check"))

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command in ("rollback", "promote"):
            return cmd_set(args, promote=args.command == "promote")
        if args.command == "geo":
            return cmd_geo(args)
        if args.command == "gc":
            return cmd_gc(args)
        return cmd_lint(args)
    except (ContractError, ValueError, RuntimeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
