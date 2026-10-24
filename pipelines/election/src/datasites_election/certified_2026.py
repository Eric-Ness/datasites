"""Importer for official 2026 general-election results (November 3, 2026), county by county.

Each state's official results file is read by an adapter (adapters/: Washington and Maryland so far)
into the county-level schema of county_schema.py, the same columns and conventions as MEDSL's county
presidential file. Every row also carries:
- status: "certified" once the state has certified the results, "unofficial" before that;
- source_url: the exact official file the row came from;
- certified_at: the date the state certified (null while unofficial).

Certification is a fact about a state's process, not something the results files say: Washington's
export and Maryland's CSVs look the same before and after. So RELEASES records it, entered by hand
from the state's own certification document. collect() passes the release record to transform() as a
raw input, so editing it changes the content fingerprint and republishes the dataset with no
version bump.

There is no 2026 contract yet. A dataset with no data would block every run, and states don't
certify until mid-November to mid-December 2026 (Washington's deadline is December 3; Maryland's
Board of State Canvassers certified on December 5 in 2024). There is one dataset per release:
county-results-2026-<key>, where <key> is the RELEASES key (WA, MD). Each state is its own source
with its own terms. The driver adds one like this:

1. Unofficial results, optional, from election night: leave the release's certified_at as None and
   do steps 3 and 4. The rows say status = unofficial.
2. Certified results: when the state certifies, open its certification document (Washington: the
   Secretary of State's certification announcement and signed certificate; Maryland: the Board of
   State Canvassers' certification). Then set RELEASES["WA"] = Release("WA", certified_at="2026-12-03",
   certification_url="https://..."), using the date and URL from that document. If the contract
   already exists, the next run republishes every row as certified. Otherwise do steps 3 and 4.
3. Write the contract with
       uv run python -m datasites_election.certified_2026 contract WA \\
           > ../contracts/sites/electiondata/datasets/county-results-2026-wa.yaml
   (run from pipelines/). The output is a complete contract: the adapter's source block (license,
   commercial_ok and attribution read from the state's terms page), the schema, the key and checks.
   Review it before publishing. Maryland's commercial_ok is null because its site has a copyright
   notice and no reuse terms, so decide or ask first.
4. Run uv run datasites run electiondata/county-results-2026-wa --target local. Check a few
   statewide totals against the state's certified canvass. Then add those facts to
   scripts/verify/election.mjs.
5. Special elections: list them in Release.special, e.g. special=(("US SENATE", "STATEWIDE"),).
   A runoff (Georgia, Louisiana) is its own release under its own key, for example
   RELEASES["GA-RUNOFF"] = Release("GA", election_date="2026-12-01", stage="RUNOFF"), which becomes
   dataset county-results-2026-ga-runoff.
6. For another state, write adapters/<st>.py (adapters/base.py has the interface). Add it to ADAPTERS
   and RELEASES, and test it against that state's certified 2024 file in the same format.

After a state certifies, results normally stay fixed. Later changes (recounts, court orders) are
republished, and earlier versions stay available through the manifest.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import polars as pl
import yaml

from . import county_schema
from .adapters import ADAPTERS, StateAdapter, to_county_rows

ELECTION_DATE = "2026-11-03"
DATASET_PREFIX = "county-results-2026-"
STATUS_COLUMNS: list[tuple[str, str, bool, str]] = [
    ("status", "str", False, "certified once the state certified these results; unofficial before that"),
    ("source_url", "str", False, "The official results file this row was read from"),
    ("certified_at", "date", True, "Date the state certified the results; null while unofficial"),
]


@dataclass(frozen=True)
class Release:
    """One state's results for one election, and whether they are certified (entered from the certification document)."""

    state_po: str
    election_date: str = ELECTION_DATE
    certified_at: str | None = None
    certification_url: str | None = None
    stage: str = "GEN"
    special: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (office, district) pairs

    def __post_init__(self) -> None:
        held = date.fromisoformat(self.election_date)
        if self.stage not in ("GEN", "RUNOFF"):
            raise ValueError(f"{self.state_po}: stage must be GEN or RUNOFF, not {self.stage!r}")
        if self.certified_at is not None:
            if date.fromisoformat(self.certified_at) < held:
                raise ValueError(f"{self.state_po}: certified_at {self.certified_at} is before the election ({self.election_date})")
            if not self.certification_url:
                raise ValueError(f"{self.state_po}: a certified release needs the certification_url it was read from")

    @property
    def status(self) -> str:
        return "certified" if self.certified_at else "unofficial"


# Filled in by hand from each state's certification document (see the module docstring).
RELEASES: dict[str, Release] = {
    "WA": Release("WA"),
    "MD": Release("MD"),
}


def dataset_id(key: str) -> str:
    return f"{DATASET_PREFIX}{key.lower()}"


def release_key(contract_id: str) -> str:
    """'county-results-2026-wa' -> 'WA'; 'county-results-2026-ga-runoff' -> 'GA-RUNOFF'."""
    if not contract_id.startswith(DATASET_PREFIX):
        raise ValueError(f"{contract_id}: certified-results datasets are named {DATASET_PREFIX}<release key>")
    key = contract_id.removeprefix(DATASET_PREFIX).upper()
    if key not in RELEASES:
        raise ValueError(f"{contract_id}: no release {key!r} in RELEASES (have {sorted(RELEASES)})")
    if RELEASES[key].state_po not in ADAPTERS:
        raise ValueError(f"{contract_id}: no adapter for {RELEASES[key].state_po} (have {sorted(ADAPTERS)})")
    return key


def collect(ctx) -> dict[str, bytes]:
    release = RELEASES[release_key(ctx.contract.id)]
    urls = ADAPTERS[release.state_po].resources(date.fromisoformat(release.election_date))
    raw = {f"{release.state_po}/{name}": ctx.get(url) for name, url in urls.items()}
    raw["release"] = json.dumps({**asdict(release), "resources": urls}, sort_keys=True).encode()
    return raw


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    info: dict[str, Any] = json.loads(raw["release"])
    urls: dict[str, str] = info.pop("resources")
    release = Release(**{**info, "special": tuple(tuple(pair) for pair in info["special"])})
    prefix = f"{release.state_po}/"
    files = {name.removeprefix(prefix): body for name, body in raw.items() if name.startswith(prefix)}
    return build(ADAPTERS[release.state_po], files, release, urls)


def build(adapter: StateAdapter, files: dict[str, bytes], release: Release, urls: dict[str, str]) -> pl.DataFrame:
    if set(files) != set(urls):
        raise ValueError(f"{release.state_po}: files {sorted(files)} don't match resources {sorted(urls)}")
    held = date.fromisoformat(release.election_date)
    rows = to_county_rows(adapter, files, held.year, stage=release.stage, special=set(release.special))
    offices = set(rows.select("office", "district").unique().iter_rows())
    unknown = sorted(set(release.special) - offices)
    if unknown:
        raise ValueError(f"{release.state_po}: special elections {unknown} are not in the results")
    return rows.select(
        *county_schema.names(),
        pl.lit(release.status).alias("status"),
        pl.col("resource").replace_strict(urls).alias("source_url"),
        pl.lit(release.certified_at, dtype=pl.Utf8).alias("certified_at"),
    )


def columns() -> list[tuple[str, str, bool, str]]:
    return [*county_schema.COLUMNS, *STATUS_COLUMNS]


def contract(key: str) -> dict[str, Any]:
    """The contract for one release, ready to write to contracts/sites/electiondata/datasets/."""
    release = RELEASES[key]
    adapter = ADAPTERS[release.state_po]
    name = county_schema.state_name(release.state_po).title()
    held = date.fromisoformat(release.election_date)
    election = f"{held:%B} {held.day}, {held.year} {'runoff' if release.stage == 'RUNOFF' else 'general'} election"
    schema = []
    for column, kind, nullable, description in columns():
        entry: dict[str, Any] = {"name": column, "type": kind}
        if column in ("candidatevotes", "totalvotes"):
            entry["unit"] = "votes"
        if nullable:
            entry["nullable"] = True
        entry["description"] = description
        schema.append(entry)
    source = adapter.source
    return {
        "id": dataset_id(key),
        "version": 1,
        "kind": "table",
        "title": f"{name} {held.year} {'runoff' if release.stage == 'RUNOFF' else 'general election'} results by county",
        "description": (
            f"Official results of the {election} in {name}, by county, for federal and statewide offices, "
            f"from the state's own results files. Each row says whether the results are certified or still unofficial."
        ),
        "pipeline": "datasites_election.certified_2026",
        "check_every": "P1D",  # change to P7D once the state has certified
        "cadence": "P1Y",
        "sla": "P2Y",
        "source": {
            "id": source.id,
            "name": source.name,
            "publisher": source.publisher,
            "url": source.url,
            "license": source.license,
            "commercial_ok": source.commercial_ok,
            "attribution": source.attribution,
        },
        "key": list(county_schema.KEY),
        "schema": schema,
        "checks": {
            "min_rows": 2 * len(county_schema.counties(release.state_po)),
            "rows_min_ratio": 0.98,
            "ranges": {"candidatevotes": [0, 20000000], "totalvotes": [0, 20000000]},
            "max_null_fraction": {"candidatevotes": 0, "totalvotes": 0, **({"county_id": 0} if adapter.reports_by_county else {})},
        },
    }


def contract_yaml(key: str) -> str:
    terms = ADAPTERS[RELEASES[key].state_po].source.terms_url
    header = (
        f"# Generated by: uv run python -m datasites_election.certified_2026 contract {key}\n"
        f"# license and commercial_ok come from {terms}\n"
    )
    return header + yaml.safe_dump(contract(key), sort_keys=False, allow_unicode=True, width=110)


def main(argv: list[str]) -> int:
    key = argv[1].upper() if len(argv) == 2 else ""
    if len(argv) != 2 or argv[0] != "contract" or key not in RELEASES or not re.fullmatch(r"[A-Z]{2}(-[A-Z]+)?", key):
        print(f"usage: python -m datasites_election.certified_2026 contract <{'|'.join(sorted(RELEASES))}>", file=sys.stderr)
        return 2
    sys.stdout.write(contract_yaml(key))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
