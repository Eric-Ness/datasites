"""Dataset contracts: one YAML file per dataset under contracts/sites/<site>/datasets/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

COLUMN_TYPES = {"month", "date", "float", "int", "str", "bool"}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    license: str
    commercial_ok: bool | None
    attribution: str
    publisher: str | None = None


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    unit: str | None = None
    nullable: bool = False


@dataclass(frozen=True)
class Checks:
    rows_min_ratio: float | None = None
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    max_null_fraction: dict[str, float] = field(default_factory=dict)
    newest_not_older: bool = True
    revisions_max_changed_fraction: float | None = None
    revisions_tolerance: float = 0.0


@dataclass(frozen=True)
class Contract:
    site: str
    id: str
    version: int
    title: str
    description: str
    pipeline: str
    check_every: str
    cadence: str
    sla: str
    source: Source
    series: dict[str, str]
    locations: dict[str, str]
    schema: list[Column]
    checks: Checks
    discontinued: bool = False

    @property
    def ref(self) -> str:
        return f"{self.site}/{self.id}"

    @property
    def columns(self) -> list[str]:
        """Columns of the long table a pipeline's transform() returns."""
        return ["series", "location", *[c.name for c in self.schema]]


def repo_root() -> Path:
    """The checkout root: the nearest parent holding contracts/sites (or $DATASITES_ROOT)."""
    if env := os.environ.get("DATASITES_ROOT"):
        return Path(env)
    for start in (Path.cwd(), Path(__file__).resolve()):
        for parent in (start, *start.parents):
            if (parent / "contracts" / "sites").is_dir():
                return parent
    raise ContractError("cannot find the repo root (no contracts/sites directory above here)")


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    value = data.get(key)
    if value is None or value == "":
        raise ContractError(f"{where}: missing required field '{key}'")
    return value


def parse_contract(site: str, data: dict[str, Any], where: str) -> Contract:
    src = _require(data, "source", where)
    if "commercial_ok" not in src:
        raise ContractError(f"{where}: source.commercial_ok is required (true, false or null for unknown)")
    source = Source(
        id=_require(src, "id", where),
        name=_require(src, "name", where),
        url=_require(src, "url", where),
        license=_require(src, "license", where),
        commercial_ok=src["commercial_ok"],
        attribution=" ".join(str(_require(src, "attribution", where)).split()),
        publisher=src.get("publisher"),
    )

    schema = []
    for col in _require(data, "schema", where):
        if col.get("type") not in COLUMN_TYPES:
            raise ContractError(f"{where}: column {col.get('name')!r} has unknown type {col.get('type')!r}")
        schema.append(Column(col["name"], col["type"], col.get("unit"), bool(col.get("nullable", False))))
    if not schema or schema[0].name != "date":
        raise ContractError(f"{where}: the first schema column must be 'date'")

    raw_checks = data.get("checks") or {}
    revisions = raw_checks.get("revisions") or {}
    checks = Checks(
        rows_min_ratio=raw_checks.get("rows_min_ratio"),
        ranges={k: (float(v[0]), float(v[1])) for k, v in (raw_checks.get("ranges") or {}).items()},
        max_null_fraction={k: float(v) for k, v in (raw_checks.get("max_null_fraction") or {}).items()},
        newest_not_older=bool(raw_checks.get("newest_not_older", True)),
        revisions_max_changed_fraction=revisions.get("max_changed_fraction"),
        revisions_tolerance=float(revisions.get("tolerance", 0.0)),
    )

    contract = Contract(
        site=site,
        id=_require(data, "id", where),
        version=int(_require(data, "version", where)),
        title=_require(data, "title", where),
        description=" ".join(str(_require(data, "description", where)).split()),
        pipeline=_require(data, "pipeline", where),
        check_every=_require(data, "check_every", where),
        cadence=_require(data, "cadence", where),
        sla=_require(data, "sla", where),
        source=source,
        series=dict(_require(data, "series", where)),
        locations=dict(_require(data, "locations", where)),
        schema=schema,
        checks=checks,
        discontinued=bool(data.get("discontinued", False)),
    )
    for name in [*checks.ranges, *checks.max_null_fraction]:
        if name not in {c.name for c in schema}:
            raise ContractError(f"{where}: check refers to unknown column {name!r}")
    return contract


def load_contract(ref: str, root: Path | None = None) -> Contract:
    """Loads 'climatedata/co2-monthly'."""
    site, _, dataset = ref.partition("/")
    if not site or not dataset:
        raise ContractError(f"expected <site>/<dataset>, got {ref!r}")
    path = (root or repo_root()) / "contracts" / "sites" / site / "datasets" / f"{dataset}.yaml"
    if not path.is_file():
        raise ContractError(f"no contract at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    contract = parse_contract(site, data, str(path))
    if contract.id != dataset:
        raise ContractError(f"{path}: id {contract.id!r} must match the file name")
    return contract


def site_contracts(site: str, root: Path | None = None) -> list[Contract]:
    folder = (root or repo_root()) / "contracts" / "sites" / site / "datasets"
    if not folder.is_dir():
        return []
    return [load_contract(f"{site}/{p.stem}", root) for p in sorted(folder.glob("*.yaml"))]


def all_contracts(root: Path | None = None) -> list[Contract]:
    sites_dir = (root or repo_root()) / "contracts" / "sites"
    return [c for site in sorted(p.name for p in sites_dir.iterdir() if p.is_dir()) for c in site_contracts(site, root)]
