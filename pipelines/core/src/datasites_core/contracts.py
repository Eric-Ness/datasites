"""Dataset contracts: one YAML file per dataset under contracts/sites/<site>/datasets/.

Three kinds of dataset:
  series  a long table of (series, location, date, values...): time series per place. The default.
  table   keyed records (emission factors, a satellite catalog, election results), optionally
          split into partitions.
  files   prebuilt outputs (map boundaries); the pipeline returns the files themselves.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

COLUMN_TYPES = {"month", "date", "year", "datetime", "float", "int", "str", "bool"}
KINDS = ("series", "table", "files")
GEO_LEVELS = ("world", "country", "us-state", "us-county")


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
    description: str | None = None


@dataclass(frozen=True)
class Checks:
    rows_min_ratio: float | None = None
    min_rows: int | None = None
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    max_null_fraction: dict[str, float] = field(default_factory=dict)
    newest_not_older: bool = True
    revisions_max_changed_fraction: float | None = None
    revisions_tolerance: float = 0.0
    required_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class Locations:
    """Where a series dataset's location ids come from.

    explicit  a fixed {id: name} map in the contract
    registry  every location of these geo-registry levels (packages/geo), e.g. ("us-county",)
    pattern   ids matching a regex (station networks); names come from the transform's
              optional `location_name` column
    """

    explicit: dict[str, str] = field(default_factory=dict)
    registry: tuple[str, ...] = ()
    pattern: str | None = None

    def names(self) -> dict[str, str]:
        """Known id -> name map (explicit plus registry). Pattern-based ids are not enumerable."""
        names = dict(self.explicit)
        if self.registry:
            from . import geo

            names.update({loc_id: loc["name"] for loc_id, loc in geo.load().items() if loc["level"] in self.registry})
        return names

    def allows(self, location_id: str, known: dict[str, str] | None = None) -> bool:
        known = self.names() if known is None else known
        if location_id in known:
            return True
        return bool(self.pattern and re.fullmatch(self.pattern, location_id))


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
    schema: list[Column]
    checks: Checks
    kind: str = "series"
    series: dict[str, str] = field(default_factory=dict)
    # Per-series unit for the columns that have one, where series measure different things in one column
    # (county-climate: degF for temperatures, in for precipitation). Columns keep their own unit otherwise.
    series_units: dict[str, str] = field(default_factory=dict)
    locations: Locations = field(default_factory=Locations)
    key: tuple[str, ...] = ()
    partition: str | None = None
    raw_archive_max_mb: int = 100
    requires_env: tuple[str, ...] = ()
    discontinued: bool = False

    @property
    def ref(self) -> str:
        return f"{self.site}/{self.id}"

    @property
    def columns(self) -> list[str]:
        """Columns of the table a series or table pipeline's transform() returns."""
        if self.kind == "series":
            return ["series", "location", *[c.name for c in self.schema]]
        return [c.name for c in self.schema]

    @property
    def time_column(self) -> str | None:
        """The column coverage is measured on: 'date' for series, the first time-typed column for tables."""
        for col in self.schema:
            if col.type in ("month", "date", "year", "datetime"):
                return col.name
        return None


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


def _locations(raw: Any, where: str) -> Locations:
    if raw is None:
        return Locations()
    if not isinstance(raw, dict):
        raise ContractError(f"{where}: locations must be a mapping")
    if "registry" in raw or "pattern" in raw:
        registry = raw.get("registry") or ()
        registry = (registry,) if isinstance(registry, str) else tuple(registry)
        unknown = [level for level in registry if level not in GEO_LEVELS]
        if unknown:
            raise ContractError(f"{where}: unknown registry level(s) {unknown}; expected {GEO_LEVELS}")
        pattern = raw.get("pattern")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as err:
                raise ContractError(f"{where}: invalid locations.pattern: {err}") from err
        return Locations(explicit=dict(raw.get("explicit") or {}), registry=registry, pattern=pattern)
    return Locations(explicit={str(k): str(v) for k, v in raw.items()})


def parse_contract(site: str, data: dict[str, Any], where: str) -> Contract:
    kind = data.get("kind", "series")
    if kind not in KINDS:
        raise ContractError(f"{where}: kind must be one of {KINDS}")

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
    for col in data.get("schema") or []:
        if col.get("type") not in COLUMN_TYPES:
            raise ContractError(f"{where}: column {col.get('name')!r} has unknown type {col.get('type')!r}")
        schema.append(
            Column(col["name"], col["type"], col.get("unit"), bool(col.get("nullable", False)), col.get("description"))
        )
    names = [c.name for c in schema]
    if len(names) != len(set(names)):
        raise ContractError(f"{where}: duplicate schema column names")

    series = dict(data.get("series") or {})
    series_units = {str(k): str(v) for k, v in (data.get("series_units") or {}).items()}
    unknown_units = sorted(set(series_units) - set(series))
    if unknown_units:
        raise ContractError(f"{where}: series_units names series not in 'series': {unknown_units}")
    if series_units and not any(c.unit for c in schema):
        raise ContractError(f"{where}: series_units needs at least one schema column with a unit to apply to")
    locations = _locations(data.get("locations"), where)
    key = tuple(data.get("key") or ())
    partition = data.get("partition")
    if kind == "series":
        if not schema or schema[0].name != "date":
            raise ContractError(f"{where}: the first schema column of a series dataset must be 'date'")
        if not series:
            raise ContractError(f"{where}: a series dataset needs a 'series' map")
        if not (locations.explicit or locations.registry or locations.pattern):
            raise ContractError(f"{where}: a series dataset needs 'locations' (a map, a registry level or a pattern)")
        if {"series", "location", "location_name"} & set(names):
            raise ContractError(f"{where}: 'series', 'location' and 'location_name' are reserved column names")
    elif kind == "table":
        if not schema:
            raise ContractError(f"{where}: a table dataset needs a schema")
        if not key:
            raise ContractError(f"{where}: a table dataset needs a 'key' (list of columns)")
        needed = list(key) + ([partition] if partition else [])
        missing = [k for k in needed if k not in names]
        if missing:
            raise ContractError(f"{where}: key/partition columns not in schema: {missing}")

    raw_checks = data.get("checks") or {}
    revisions = raw_checks.get("revisions") or {}
    checks = Checks(
        rows_min_ratio=raw_checks.get("rows_min_ratio"),
        min_rows=raw_checks.get("min_rows"),
        ranges={k: (float(v[0]), float(v[1])) for k, v in (raw_checks.get("ranges") or {}).items()},
        max_null_fraction={k: float(v) for k, v in (raw_checks.get("max_null_fraction") or {}).items()},
        newest_not_older=bool(raw_checks.get("newest_not_older", kind == "series")),
        revisions_max_changed_fraction=revisions.get("max_changed_fraction"),
        revisions_tolerance=float(revisions.get("tolerance", 0.0)),
        required_files=tuple(raw_checks.get("required_files") or ()),
    )
    for name in [*checks.ranges, *checks.max_null_fraction]:
        if name not in names:
            raise ContractError(f"{where}: check refers to unknown column {name!r}")
    if kind == "files" and not checks.required_files:
        raise ContractError(f"{where}: a files dataset needs checks.required_files")

    return Contract(
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
        schema=schema,
        checks=checks,
        kind=kind,
        series=series,
        series_units=series_units,
        locations=locations,
        key=key,
        partition=partition,
        raw_archive_max_mb=int(data.get("raw_archive_max_mb", 100)),
        requires_env=tuple(data.get("requires_env") or ()),
        discontinued=bool(data.get("discontinued", False)),
    )


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
