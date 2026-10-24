"""Shared reading of Jonathan McDowell's GCAT TSV files (https://planet4589.org/space/gcat/).

Every GCAT TSV starts with a '#'-prefixed column header and a '# Updated <date>' line, then one
tab-separated record per line with fixed-width padding inside the fields. Missing values are '-'.
"""

from __future__ import annotations

import io

import polars as pl

from .vague import parse

BASE = "https://planet4589.org/space/gcat/tsv"
URLS = {
    "satcat": f"{BASE}/cat/satcat.tsv",  # S objects, US catalog numbers 1-69999
    "satcat100k": f"{BASE}/cat/satcat100k.tsv",  # S objects numbered from 100000 (since the July 2026 rollover)
    "satcat270k": f"{BASE}/cat/satcat270k.tsv",  # S objects: Space Force analyst objects numbered from 270000
    "auxcat": f"{BASE}/cat/auxcat.tsv",  # A objects: in orbit but not in the US catalog
    "tmpcat": f"{BASE}/cat/tmpcat.tsv",  # T objects: expected to get an S number soon
    "ftocat": f"{BASE}/cat/ftocat.tsv",  # F objects: failed to reach orbit
    "ecat": f"{BASE}/cat/ecat.tsv",  # later phases of objects in the catalogs above
    "currentcat": f"{BASE}/derived/currentcat.tsv",  # GCAT's own summary of each S/A/T object's current state
    "orgs": f"{BASE}/tables/orgs.tsv",  # organization and country codes
    "launch": f"{BASE}/launch/launch.tsv",  # the launch list: every launch, orbital or not
}


def normalize(name: str, body: bytes) -> bytes:
    """Drops the '# Updated 2026 Oct 24 0114:13' line, which changes on every regeneration, before change detection."""
    return b"\n".join(line for line in body.split(b"\n") if not line.startswith(b"# Updated"))


def updated_at(body: bytes) -> str | None:
    """GCAT's own regeneration time from the '# Updated 2026 Oct 24 0114:13' line, as an ISO day ('2026-10-24').

    Used to refuse dates that cannot have happened yet: a launch 'on 2027 Jan 3' in a file regenerated on
    2026 Oct 24 is a typo, and publishing it as a date would invent a fact. The text is still kept.
    """
    for line in body.split(b"\n", 5)[:5]:
        if line.startswith(b"# Updated"):
            parsed = parse(line[len(b"# Updated") :].decode("ascii", "replace").strip())
            return parsed.end.date().isoformat() if parsed else None
    return None


def read_tsv(body: bytes, name: str = "gcat") -> pl.DataFrame:
    """A GCAT TSV as an all-string table with every field trimmed. Comment lines are dropped.

    Raises if the header is missing or a record has the wrong number of fields, so a truncated or
    reformatted file fails loudly instead of shifting columns.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:  # GCAT's files are ASCII; a stray Windows-1252 byte should not stop the catalog
        text = body.decode("cp1252", errors="replace")
    lines = text.split("\n")
    header_index = next((i for i, line in enumerate(lines) if line.startswith("#") and "\t" in line), None)
    if header_index is None:
        raise ValueError(f"{name}: no '#'-prefixed column header line")
    header = [h.strip() for h in lines[header_index].lstrip("#").rstrip("\r").split("\t")]
    if len(set(header)) != len(header):
        raise ValueError(f"{name}: duplicate column names in header {header}")
    data = [line.rstrip("\r") for line in lines[header_index + 1 :] if line.strip() and not line.startswith("#")]
    for number, line in enumerate(data, 1):
        if line.count("\t") != len(header) - 1:
            raise ValueError(f"{name}: record {number} has {line.count(chr(9)) + 1} fields, header has {len(header)}: {line[:80]!r}")
    if not data:
        return pl.DataFrame({h: pl.Series([], dtype=pl.Utf8) for h in header})
    frame = pl.read_csv(
        io.StringIO("\n".join(data)),
        separator="\t",
        has_header=False,
        new_columns=header,
        infer_schema=False,
        quote_char=None,
    )
    return frame.with_columns(pl.all().str.strip_chars())


def lookup(column: pl.Expr, fn) -> pl.Expr:
    """Applies a Python function once per distinct value (dates and names repeat thousands of times)."""
    def apply(series: pl.Series) -> pl.Series:
        values = series.unique().drop_nulls().to_list()
        if not values:
            return pl.Series(series.name, [None] * len(series), dtype=pl.Utf8)
        keys = pl.Series("k", values, dtype=pl.Utf8)
        mapped = pl.Series("v", [fn(v) for v in values], dtype=pl.Utf8)
        return series.replace_strict(keys, mapped, default=None, return_dtype=pl.Utf8)

    return column.map_batches(apply, return_dtype=pl.Utf8)


def missing(column: str) -> pl.Expr:
    """GCAT's '-' (and an empty field) as null."""
    col = pl.col(column)
    return pl.when(col.is_in(["-", ""])).then(None).otherwise(col)


def number(column: str) -> pl.Expr:
    """A numeric GCAT field as a float; '-' and blanks become null, and so does 'Inf' (GCAT's apogee of an
    escape trajectory, which JSON cannot carry; the orbit category still says EEO)."""
    value = missing(column)
    return pl.when(value.str.contains(r"^[-+]?Inf$")).then(None).otherwise(value).cast(pl.Float64, strict=True)


def org_names(orgs_body: bytes) -> dict[str, str]:
    """Org code -> English name from GCAT's orgs table (e.g. 'JSC' -> 'NASA Johnson Space Flight Center')."""
    orgs = read_tsv(orgs_body, "orgs")
    names: dict[str, str] = {}
    blank = lambda v: v is None or not v.replace("-", "").strip()  # noqa: E731  ('-', or '-   -' in a few rows)
    for code, ename, name in orgs.select("Code", "EName", "Name").iter_rows():
        label = name if blank(ename) else ename
        if code and not blank(label):
            names[code] = " ".join(label.split())
    return names


def org_name(code: str | None, names: dict[str, str]) -> str | None:
    """Name of an org code; joint owners like 'SFSSC/CIA' are named part by part. None if any part is unknown.

    A trailing '?' (GCAT's mark for an uncertain attribution, e.g. 'GSFC?') is ignored for the lookup.
    """
    if code is None:
        return None
    code = code.rstrip("?")
    if code in names:
        return names[code]
    parts = code.split("/")
    if len(parts) > 1 and all(p in names for p in parts):
        return " / ".join(names[p] for p in parts)
    return None
