"""Shared helpers for MIT Election Data + Science Lab (MEDSL) files on Harvard Dataverse.

MEDSL publishes each dataset as a versioned Dataverse record (doi:10.7910/DVN/...). The contract's
attribution is the Dataverse citation, which names the version ("Harvard Dataverse, V8"), so a
pipeline downloads exactly that version and stops with a clear error when MEDSL publishes a new one.
A person then reviews the change, updates the citation and bumps the contract version.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

DATAVERSE = "https://dataverse.harvard.edu"
CITED_VERSION = re.compile(r"Harvard Dataverse, V(\d+)(?:\.(\d+))?\b")


def dataset_api_url() -> str:
    """Dataverse's native API for a dataset's metadata; pass persistentId=doi:... as a parameter."""
    return f"{DATAVERSE}/api/datasets/:persistentId/"


def access_url(file_id: int) -> str:
    """The data-access API. format=original returns the file as MEDSL uploaded it (CSV), not Dataverse's .tab copy."""
    return f"{DATAVERSE}/api/access/datafile/{file_id}?format=original"


def cited_version(attribution: str) -> tuple[int, int]:
    """'..., Harvard Dataverse, V8, UNF:...' -> (8, 0)."""
    m = CITED_VERSION.search(attribution)
    if not m:
        raise ValueError(f"the attribution does not cite a Dataverse version ('Harvard Dataverse, V<n>'): {attribution!r}")
    return int(m.group(1)), int(m.group(2) or 0)


def resolve_file(meta: dict[str, Any], doi: str, filename: str, attribution: str) -> int:
    """The id of `filename` in the latest released version, which must be the version the contract cites."""
    data = meta.get("data") or {}
    latest = data.get("latestVersion") or {}
    if latest.get("versionState") != "RELEASED":
        raise RuntimeError(f"{doi}: the latest version is {latest.get('versionState')!r}, not RELEASED")
    found = (int(latest.get("versionNumber", -1)), int(latest.get("versionMinorNumber", 0)))
    cited = cited_version(attribution)
    if found != cited:
        raise RuntimeError(
            f"{doi}: MEDSL has published V{found[0]}.{found[1]} but the contract cites V{cited[0]}.{cited[1]}. "
            "Review the new version, then update source.attribution to its Dataverse citation and bump the contract version."
        )
    if data.get("guestbookId") or latest.get("guestbookId"):
        raise RuntimeError(f"{doi}: the dataset now requires a guestbook response, so it can't be downloaded automatically")
    for item in latest.get("files") or []:
        datafile = item.get("dataFile") or {}
        if filename in (datafile.get("originalFileName"), datafile.get("filename")):
            if item.get("restricted") or datafile.get("restricted"):
                raise RuntimeError(f"{doi}: {filename} is restricted")
            return int(datafile["id"])
    names = sorted((i.get("dataFile") or {}).get("filename", "?") for i in latest.get("files") or [])
    raise RuntimeError(f"{doi}: no file named {filename} in V{found[0]}.{found[1]} (files: {names})")


# --- parsing --------------------------------------------------------------------------------------


def require_columns(df: pl.DataFrame, expected: list[str], what: str) -> None:
    """MEDSL occasionally renames or adds columns between versions; fail loudly rather than misread."""
    if df.columns != expected:
        missing = [c for c in expected if c not in df.columns]
        extra = [c for c in df.columns if c not in expected]
        raise ValueError(f"{what}: columns changed (missing {missing}, unexpected {extra}); got {df.columns}")


def require_values(df: pl.DataFrame, column: str, allowed: set[str], what: str) -> None:
    seen = set(df[column].drop_nulls().unique().to_list())
    unknown = sorted(seen - allowed)
    if unknown:
        raise ValueError(f"{what}: unexpected {column} values {unknown[:10]}")


def flag(column: str) -> pl.Expr:
    """MEDSL's 'True'/'False' (any case) as a boolean. Check the values with require_values first."""
    return pl.col(column).str.to_lowercase() == "true"


def whole_number(column: str, what: str, df: pl.DataFrame) -> pl.Expr:
    """MEDSL writes counts as floats ('7310.0'). Returns them as Int64 after checking each is whole."""
    values = df[column].cast(pl.Float64, strict=False)
    bad = df.filter(values.is_null() & df[column].is_not_null())[column]
    if len(bad):
        raise ValueError(f"{what}: {column} has non-numeric values {bad.unique().to_list()[:5]}")
    fractional = values.drop_nulls().filter(values.drop_nulls() != values.drop_nulls().round(0))
    if len(fractional):
        raise ValueError(f"{what}: {column} has fractional counts {fractional.to_list()[:5]}")
    return pl.col(column).cast(pl.Float64).cast(pl.Int64)


def version_date(df: pl.DataFrame, column: str, what: str) -> pl.Expr:
    """MEDSL's version stamp, which is the date the file was finalized, as YYYY-MM-DD.

    Older rows use YYYYMMDD ('20210114'); rows added in 2025 use M/D/YY ('11/20/25')."""
    parsed = pl.coalesce(
        pl.col(column).str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col(column).str.strptime(pl.Date, "%m/%d/%y", strict=False),
    )
    bad = df.filter(df.select(parsed.is_null()).to_series() & df[column].is_not_null())[column]
    if len(bad):
        raise ValueError(f"{what}: unrecognized {column} values {bad.unique().to_list()[:5]}")
    return parsed.dt.strftime("%Y-%m-%d")
