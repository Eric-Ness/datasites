"""Home Mortgage Disclosure Act (HMDA) lending by county, state and nation, from the CFPB's yearly snapshot of
the public loan-level data (2018 on, the current reporting format).

Source: https://files.ffiec.cfpb.gov/static-data/snapshot/<year>/<year>_public_lar_csv.zip, one zipped CSV per
year (0.6 to 0.9 GB zipped, 4 to 6 GB unzipped) with one row per application or loan reported by every HMDA
filer. The snapshot is taken in the spring after the reporting year closes (2025's came out in June 2026).

What is counted, by year, place and loan purpose:
- applications: actions 1 to 5 (originated, approved but not accepted, denied, withdrawn, closed as
  incomplete). Loans a lender bought from another lender (action 6) are left out, because they were already
  reported as originations by the lender that made them; so are preapproval requests (7, 8).
- originations (action 1), denials (action 3), and the denial rate: denials as a percent of applications
  that reached a decision (actions 1 to 3).
- originated_amount: the sum of loan_amount over originations, and median_originated_amount. HMDA's public
  loan amounts are the midpoint of a $10,000 band (CFPB's modified LAR), so both are approximate.
Loan purposes are HMDA's codes: 1 home purchase, 2 home improvement, 31 refinancing, 32 cash-out refinancing,
4 other purpose. Records without a county (county_code "NA") count toward their state and the nation only.

The yearly files are far too big to pass through the engine whole, so collect() does the heavy work: it
streams each year's zip to a temporary file, aggregates it with polars, and keeps only that year's table as
raw input, with the snapshot's URL and ETag on every row (source_url, source_etag). A year whose snapshot
ETag hasn't changed since the published version reuses that version's rows (ctx.previous_files and
ctx.read_blob) instead of downloading again, so a normal weekly run makes one small request per year.
transform() only reads those tables back.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from datasites_core import fetch, geo

SNAPSHOT = "https://files.ffiec.cfpb.gov/static-data/snapshot/{year}/{year}_public_lar_csv.zip"
FIRST_YEAR = 2018  # the first year in the current (2018) reporting format
PURPOSES = {"1": "home-purchase", "2": "home-improvement", "31": "refinance", "32": "cash-out-refinance", "4": "other"}
APPLICATIONS = ["1", "2", "3", "4", "5"]
DECIDED = ["1", "2", "3"]
COLUMNS = [
    "year",
    "location",
    "loan_purpose",
    "applications",
    "originations",
    "denials",
    "denial_rate",
    "originated_amount",
    "median_originated_amount",
    "source_url",
    "source_etag",
]
SCHEMA = {
    "year": pl.Utf8,
    "location": pl.Utf8,
    "loan_purpose": pl.Utf8,
    "applications": pl.Int64,
    "originations": pl.Int64,
    "denials": pl.Int64,
    "denial_rate": pl.Float64,
    "originated_amount": pl.Int64,
    "median_originated_amount": pl.Int64,
    "source_url": pl.Utf8,
    "source_etag": pl.Utf8,
}


# --- the source ---------------------------------------------------------------------------------------------


def probe(url: str) -> str | None:
    """The snapshot's ETag, or None if that year's snapshot isn't out (403/404). One byte, not the whole file."""
    try:
        res = fetch.request(url, headers={"Range": "bytes=0-0"}, timeout=60)
    except httpx.HTTPStatusError as err:
        if err.response.status_code in (403, 404):
            return None
        raise
    etag = res.headers.get("etag")
    if not etag:
        raise RuntimeError(f"{url}: no ETag, so an unchanged year can't be recognised")
    return etag


def download(url: str, dest: Path, attempts: int = 4) -> str:
    """Streams url to dest; returns the ETag it was served with."""
    for attempt in range(1, attempts + 1):
        try:
            with httpx.stream("GET", url, headers={"User-Agent": fetch.user_agent()}, timeout=httpx.Timeout(300, connect=30), follow_redirects=True) as res:
                res.raise_for_status()
                with dest.open("wb") as out:
                    for chunk in res.iter_bytes(1 << 20):
                        out.write(chunk)
                return res.headers.get("etag", "")
        except (httpx.TransportError, httpx.HTTPStatusError) as err:
            status = getattr(getattr(err, "response", None), "status_code", None)
            if attempt == attempts or (status is not None and status < 500):
                raise RuntimeError(f"{url}: download failed ({type(err).__name__}{f' {status}' if status else ''})") from None
    raise AssertionError("unreachable")


# --- aggregating one year -------------------------------------------------------------------------------------


def state_ids_by_usps() -> dict[str, str]:
    return {loc["usps"]: loc_id for loc_id, loc in geo.load().items() if loc["level"] == "us-state" and loc.get("usps")}


def aggregate(csv_path: Path, year: str, url: str, etag: str) -> pl.DataFrame:
    """One year's loan-level CSV -> one row per place (county, state, nation) and loan purpose."""
    lar = (
        pl.scan_csv(csv_path, infer_schema=False)
        .select("activity_year", "state_code", "county_code", "action_taken", "loan_purpose", "loan_amount")
        .filter(pl.col("action_taken").is_in(APPLICATIONS) & pl.col("loan_purpose").is_in(list(PURPOSES)))
    )
    years = lar.select(pl.col("activity_year").unique()).collect()["activity_year"].to_list()
    if years != [year]:
        raise ValueError(f"HMDA {year} snapshot holds activity years {years}")
    states = state_ids_by_usps()
    lar = lar.with_columns(
        pl.when(pl.col("county_code").str.contains(r"^\d{5}$")).then(pl.lit("us-county-") + pl.col("county_code")).otherwise(None).alias("county"),
        pl.col("state_code").replace_strict(states, default=None).alias("state"),
        pl.col("loan_amount").cast(pl.Float64, strict=False).alias("amount"),
        pl.col("loan_purpose").replace_strict(PURPOSES).alias("purpose"),
    )

    def by(place: pl.Expr) -> pl.LazyFrame:
        originated = pl.col("action_taken") == "1"
        return (
            lar.with_columns(place.alias("location"))
            .filter(pl.col("location").is_not_null())
            .group_by("location", "purpose")
            .agg(
                pl.len().alias("applications"),
                originated.sum().alias("originations"),
                (pl.col("action_taken") == "3").sum().alias("denials"),
                pl.col("action_taken").is_in(DECIDED).sum().alias("decided"),
                pl.col("amount").filter(originated).sum().alias("originated_amount"),
                pl.col("amount").filter(originated).median().alias("median_originated_amount"),
            )
        )

    out = pl.concat([by(pl.col("county")), by(pl.col("state")), by(pl.lit("usa"))]).collect()
    return finish(out, year, url, etag)


def finish(out: pl.DataFrame, year: str, url: str, etag: str) -> pl.DataFrame:
    return out.select(
        pl.lit(year).alias("year"),
        "location",
        pl.col("purpose").alias("loan_purpose"),
        pl.col("applications").cast(pl.Int64),
        pl.col("originations").cast(pl.Int64),
        pl.col("denials").cast(pl.Int64),
        pl.when(pl.col("decided") > 0).then((100 * pl.col("denials") / pl.col("decided")).round(1)).otherwise(None).alias("denial_rate"),
        pl.col("originated_amount").round(0).cast(pl.Int64),
        pl.col("median_originated_amount").round(0).cast(pl.Int64),
        pl.lit(url).alias("source_url"),
        pl.lit(etag).alias("source_etag"),
    ).sort("location", "loan_purpose")


def read_zip(zip_path: Path, year: str, url: str, etag: str, workdir: Path) -> pl.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"HMDA {year} snapshot: expected one CSV, found {zf.namelist()}")
        csv_path = workdir / f"{year}.csv"
        with zf.open(members[0]) as src, csv_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    try:
        return aggregate(csv_path, year, url, etag)
    finally:
        csv_path.unlink(missing_ok=True)


# --- the engine's entry points ------------------------------------------------------------------------------


def to_bytes(table: pl.DataFrame) -> bytes:
    buf = io.BytesIO()
    table.select(COLUMNS).write_csv(buf)
    return buf.getvalue()


def from_bytes(body: bytes) -> pl.DataFrame:
    return pl.read_csv(io.BytesIO(body), schema=SCHEMA)


def previous_years(ctx: Any) -> dict[str, pl.DataFrame]:
    """year -> that year's rows in the published version (table/<year>.csv), if any."""
    out = {}
    for path, entry in ctx.previous_files().items():
        if path.startswith("table/") and path.endswith(".csv") and path[6:10].isdigit():
            body = ctx.read_blob(entry["blob"])
            if body:
                out[path[6:10]] = from_bytes(body)
    return out


def collect(ctx: Any) -> dict[str, bytes]:
    previous = previous_years(ctx)
    raw: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="hmda-") as tmp:
        workdir = Path(tmp)
        for year in range(FIRST_YEAR, ctx.now.year + 1):
            url = SNAPSHOT.format(year=year)
            etag = probe(url)
            if etag is None:
                if year < ctx.now.year - 1:
                    raise RuntimeError(f"HMDA {year} snapshot is missing ({url})")
                continue  # not out yet
            kept = previous.get(str(year))
            if kept is not None and kept["source_etag"].unique().to_list() == [etag]:
                raw[str(year)] = to_bytes(kept)
                continue
            zip_path = workdir / f"{year}.zip"
            served = download(url, zip_path)
            try:
                raw[str(year)] = to_bytes(read_zip(zip_path, str(year), url, served or etag, workdir))
            finally:
                zip_path.unlink(missing_ok=True)
    if not raw:
        raise RuntimeError("no HMDA snapshot found")
    return raw


def transform(raw: dict[str, bytes]) -> pl.DataFrame:
    return pl.concat([from_bytes(raw[year]) for year in sorted(raw)]).select(COLUMNS)
