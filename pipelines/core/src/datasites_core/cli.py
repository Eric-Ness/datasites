"""datasites run climatedata/co2-monthly --target local

Exit codes: 0 published or unchanged, 2 blocked by validation, 1 error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .contracts import ContractError, all_contracts, load_contract, site_contracts
from .publish import RunResult, run_dataset, set_current
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


def _summary(results: list[RunResult], failures: list[tuple[str, str]], path: str) -> None:
    lines = ["| Dataset | Outcome | Version | New blobs | Notes |", "|---|---|---|---|---|"]
    for r in results:
        notes = ""
        if r.report:
            notes = "; ".join(r.report.errors or r.report.warnings)
        lines.append(f"| {r.ref} | {r.outcome} | {r.version or ''} | {r.blobs_written} | {notes} |")
    for ref, error in failures:
        lines.append(f"| {ref} | error | | | {error} |")
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def cmd_run(args: argparse.Namespace) -> int:
    store = open_store(args.target)
    results: list[RunResult] = []
    failures: list[tuple[str, str]] = []
    for ref in _refs(args.dataset):
        try:
            result = run_dataset(load_contract(ref), store, run_url=_run_url())
            results.append(result)
            print(json.dumps(result.as_dict()))
        except Exception as err:  # keep going so one broken source doesn't stop the others
            failures.append((ref, f"{type(err).__name__}: {err}"))
            print(json.dumps({"dataset": ref, "outcome": "error", "error": str(err)}), file=sys.stderr)
    if args.summary:
        _summary(results, failures, args.summary)
    if failures:
        return 1
    return 2 if any(r.outcome == "blocked" for r in results) else 0


def cmd_set(args: argparse.Namespace, *, promote: bool) -> int:
    contract = load_contract(args.dataset)
    manifest = set_current(open_store(args.target), contract, args.version, allow_quarantined=promote)
    print(json.dumps({"dataset": contract.ref, "current": manifest["current"], "rev": manifest["rev"]}))
    return 0


def cmd_lint(_args: argparse.Namespace) -> int:
    contracts = all_contracts()
    for c in contracts:
        print(f"ok  {c.ref}  ({c.source.license}, commercial_ok={c.source.commercial_ok})")
    print(f"{len(contracts)} contracts valid")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datasites", description="Run and manage datasites pipelines.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch, validate and publish a dataset (or every dataset of a site)")
    run.add_argument("dataset", help="<site>/<dataset> or <site>")
    run.add_argument("--target", choices=TARGETS, default="local")
    run.add_argument("--summary", help="append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")

    for name, text in (("rollback", "point a dataset back at a previous version"), ("promote", "publish a reviewed quarantined version")):
        p = sub.add_parser(name, help=text)
        p.add_argument("dataset", help="<site>/<dataset>")
        p.add_argument("version")
        p.add_argument("--target", choices=TARGETS, default="local")

    sub.add_parser("lint", help="check every dataset contract")

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command in ("rollback", "promote"):
            return cmd_set(args, promote=args.command == "promote")
        return cmd_lint(args)
    except (ContractError, ValueError, RuntimeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
